import os, asyncio, logging, json, requests
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# --- 1. 基礎配置 ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562, 7738262619]
TOKEN = os.getenv("TELEGRAM_TOKEN")
GITHUB_RAW_URL = "https://raw.githubusercontent.com/boyann118-creator/sundaybot/refs/heads/main/groups.json"

(MAIN_STATE, BCAST_GROUP, BCAST_TEMP_INPUT, BCAST_MSG) = range(4)

DATA_CACHE = {"groups": [], "members": []}

def sync_from_github():
    global DATA_CACHE
    try:
        response = requests.get(GITHUB_RAW_URL, timeout=10)
        if response.status_code == 200:
            DATA_CACHE = response.json()
            logging.info("✅ 已同步最新數據庫")
            return True
        return False
    except Exception as e:
        logging.error(f"❌ 同步出錯: {e}")
        return False

# --- 2. 核心功能 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """主選單 / 總入口"""
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    
    # 確保返回時清理數據，防止狀態污染
    context.user_data.clear()
    sync_from_github()
    
    kb = [[InlineKeyboardButton("📁 查看分組名單", callback_data='list_g')],
          [InlineKeyboardButton("🚀 執行群發", callback_data='start_bc')],
          [InlineKeyboardButton("🔄 刷新 GitHub 數據", callback_data='sync_now')]]
    
    text = "🔘 **群發系統控制台 (GitHub 版)**\n系統已就緒，請選擇操作："
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    
    return MAIN_STATE

# --- 群發邏輯 ---

async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    groups = DATA_CACHE.get("groups", [])
    kb = [[InlineKeyboardButton(f"🚀 整組發送：{name}", callback_data=f"bcg_{name}")] for name in groups]
    kb.append([InlineKeyboardButton("➕ 指定群組 (輸入序號)", callback_data='bc_temp')])
    kb.append([InlineKeyboardButton("⬅️ 返回主菜單", callback_data='to_start')]) # 統一返回
    
    await update.callback_query.edit_message_text("📢 **請選擇群發模式：**", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def bc_temp_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    mems = DATA_CACHE.get("members", [])
    if not mems:
        await update.callback_query.edit_message_text("⚠️ 數據庫無成員。", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回", callback_data='to_start')]]))
        return MAIN_STATE

    temp_map = {}
    display_text = "📖 **現有群組清單 (序號參考)：**\n\n"
    for i, m in enumerate(mems, 1):
        temp_map[str(i)] = m['chat_id']
        display_text += f"<b>{i}.</b> {m['remark']} -- <i>{m['g_name']}</i>\n"

    context.user_data['temp_id_map'] = temp_map
    display_text += "\n---\n⌨️ **請輸入群組序號，用空格隔开**\n或發送 /start 取消操作。"
    
    # 這裡沒法放按鈕，因為用戶需要輸入文字，Telegram 規範建議引導用戶直接發送指令或文字
    await update.callback_query.edit_message_text(display_text, parse_mode="HTML")
    return BCAST_TEMP_INPUT

async def bc_temp_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text
    indexes = raw_text.replace(',', ' ').split()
    temp_map = context.user_data.get('temp_id_map', {})
    
    final_ids = []
    for idx in indexes:
        if idx in temp_map:
            final_ids.append(temp_map[idx])
    
    if not final_ids:
        await update.message.reply_text("❌ 無效序號，請重新輸入，或發送 /start 返回。")
        return BCAST_TEMP_INPUT
    
    context.user_data['is_temp'] = True
    context.user_data['temp_ids'] = final_ids
    await update.message.reply_text(f"✅ 已選定 {len(final_ids)} 個群組。\n\n**現在請發送你要群發的內容：**\n(發送 /start 取消)")
    return BCAST_MSG

async def bc_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = update.callback_query.data.replace("bcg_", "")
    context.user_data['bc_target'] = g_name
    context.user_data['is_temp'] = False
    await update.callback_query.answer()
    
    kb = [[InlineKeyboardButton("⬅️ 取消並返回", callback_data='to_start')]]
    await update.callback_query.edit_message_text(f"📝 **目標：{g_name}**\n\n請直接發送群發內容（文字/圖片/視頻）：", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_MSG

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('is_temp'):
        target_ids = context.user_data.get('temp_ids', [])
        tag = "臨時群組"
    else:
        g_name = context.user_data.get('bc_target')
        tag = g_name
        target_ids = [m['chat_id'] for m in DATA_CACHE.get("members", []) if m['g_name'] == g_name]

    if not target_ids:
        await update.message.reply_text("❌ 找不到目標 ID，請輸入 /start 返回。")
        return BCAST_MSG

    status_msg = await update.message.reply_text(f"🚀 正在發送至 {len(target_ids)} 個目標...")
    
    tasks = [context.bot.copy_message(chat_id=cid, from_chat_id=update.message.chat_id, message_id=update.message.message_id) for cid in target_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    success = sum(1 for r in results if not isinstance(r, Exception))
    
    await status_msg.edit_text(f"✅ **發送完成！**\n\n📍 目標：{tag}\n🎉 成功：{success}/{len(target_ids)}\n\n可繼續發送新內容，或輸入 /start 結束。")
    return BCAST_MSG

# --- 其他輔助功能 ---

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    groups = DATA_CACHE.get("groups", [])
    if not groups:
        text, kb = "⚠️ 暫無分組。", [[InlineKeyboardButton("⬅️ 返回", callback_data='to_start')]]
    else:
        text = "🗂️ **分組列表**："
        kb = [[InlineKeyboardButton(f"📂 {name}", callback_data=f"v_{name}")] for name in groups]
        kb.append([InlineKeyboardButton("⬅️ 返回主選單", callback_data='to_start')])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

async def group_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    g_name = query.data.replace("v_", "")
    mems = [m for m in DATA_CACHE.get("members", []) if m['g_name'] == g_name]
    text = f"📂 **分組：{g_name}**\n\n成員清單："
    text += "\n".join([f"• {m['remark']}" for m in mems]) if mems else "\n(空)"
    kb = [[InlineKeyboardButton("⬅️ 返回", callback_data='to_start')]] # 修改為直接回主菜單，更穩
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

# --- 啟動服務 ---
app = Flask('')
@app.route('/')
def home(): return "Bot Online"
def run_web(): app.run(host='0.0.0.0', port=8080)

def main():
    sync_from_github()
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    
    # 所有的回退/返回統一處理函數
    back_to_start_handler = CallbackQueryHandler(start, pattern='^to_start$')

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [
                CallbackQueryHandler(list_groups, pattern='^list_g$'),
                back_to_start_handler,
                CallbackQueryHandler(group_detail, pattern='^v_'),
                CallbackQueryHandler(bc_select, pattern='^start_bc$'),
                CallbackQueryHandler(lambda u,c: (sync_from_github() and u.callback_query.answer("已刷新") or start(u,c)), pattern='^sync_now$'),
            ],
            BCAST_GROUP: [
                CallbackQueryHandler(bc_get_msg, pattern='^bcg_'),
                CallbackQueryHandler(bc_temp_prompt, pattern='^bc_temp$'),
                back_to_start_handler # 確保在選擇模式時點返回有效
            ],
            BCAST_TEMP_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bc_temp_save)
            ],
            BCAST_MSG: [
                MessageHandler(filters.ALL & ~filters.COMMAND, bc_do),
                back_to_start_handler # 確保獲取消息狀態下點按鈕返回有效
            ],
        },
        fallbacks=[CommandHandler("start", start)], # 萬能 fallback，輸入 /start 必定重置
        allow_reentry=True
    )
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
