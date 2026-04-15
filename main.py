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

# 狀態定義：增加一個 BCAST_TEMP_INPUT
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
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    sync_from_github()
    context.user_data.clear()
    kb = [[InlineKeyboardButton("📁 查看分組名單", callback_data='list_g')],
          [InlineKeyboardButton("🚀 執行群發", callback_data='start_bc')],
          [InlineKeyboardButton("🔄 刷新 GitHub 數據", callback_data='sync_now')]]
    text = "🔘 **群發系統控制台 (GitHub 版)**"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

# --- 群發流程優化 ---

async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    groups = DATA_CACHE.get("groups", [])
    
    # 構建按鈕：原有分組 + 臨時群組選項
    kb = [[InlineKeyboardButton(f"🚀 發送至：{name}", callback_data=f"bcg_{name}")] for name in groups]
    kb.append([InlineKeyboardButton("➕ 使用臨時群組 (手動輸入)", callback_data='bc_temp')])
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    
    await update.callback_query.edit_message_text("📢 **選擇群發目標：**", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

# 處理點擊“臨時群組”
async def bc_temp_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("⌨️ **請輸入群組序号/ID**\n\n多個 ID 請用 **空格** 或 **逗號** 隔開。\n例如：`-100123, -100456 789012`")
    return BCAST_TEMP_INPUT

# 處理臨時 ID 的輸入
async def bc_temp_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text
    # 將逗號替換為空格，然後分割，過濾掉空字符串
    ids = raw_text.replace(',', ' ').split()
    
    if not ids:
        await update.message.reply_text("❌ 未檢測到有效的 ID，請重新輸入：")
        return BCAST_TEMP_INPUT
    
    # 存入 context.user_data 供後續發送使用
    context.user_data['is_temp'] = True
    context.user_data['temp_ids'] = ids
    
    await update.message.reply_text(f"✅ 已記錄 {len(ids)} 個臨時目標。\n\n**現在請發送群發內容：**")
    return BCAST_MSG

async def bc_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = update.callback_query.data.replace("bcg_", "")
    context.user_data['bc_target'] = g_name
    context.user_data['is_temp'] = False
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"📝 **目標分組：{g_name}**\n\n請發送群發內容：")
    return BCAST_MSG

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 判斷是發送給 GitHub 分組還是臨時群組
    if context.user_data.get('is_temp'):
        target_ids = context.user_data.get('temp_ids', [])
        display_name = "臨時群組"
    else:
        g_name = context.user_data.get('bc_target')
        display_name = g_name
        mems = [m for m in DATA_CACHE.get("members", []) if m['g_name'] == g_name]
        target_ids = [m['chat_id'] for m in mems]
    
    if not target_ids:
        await update.message.reply_text("❌ 目標列表為空，無法發送。")
        return BCAST_MSG

    status_msg = await update.message.reply_text(f"🚀 正在同時發送至 {len(target_ids)} 個目標...")
    
    tasks = []
    for cid in target_ids:
        tasks.append(
            context.bot.copy_message(
                chat_id=cid, 
                from_chat_id=update.message.chat_id, 
                message_id=update.message.message_id
            )
        )
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    success = sum(1 for r in results if not isinstance(r, Exception))
    
    await status_msg.edit_text(
        f"✅ **併發發送任務完成！**\n\n📍 目標：{display_name}\n🎉 成功：{success}/{len(target_ids)}\n\n"
        "**可繼續發送內容，或輸入 /start 返回。**"
    )
    return BCAST_MSG

# --- 基礎函數 (保持不變) ---
async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    groups = DATA_CACHE.get("groups", [])
    if not groups:
        text = "⚠️ 暫無分組。"
        kb = [[InlineKeyboardButton("⬅️ 返回", callback_data='to_start')]]
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
    if not mems: text += "\n(空)"
    else:
        for m in mems: text += f"\n• {m['remark']}"
    kb = [[InlineKeyboardButton("⬅️ 返回列表", callback_data='list_g')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

app = Flask('')
@app.route('/')
def home(): return "Bot Online"
def run_web(): app.run(host='0.0.0.0', port=8080)

def main():
    sync_from_github()
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [
                CallbackQueryHandler(list_groups, pattern='^list_g$'),
                CallbackQueryHandler(start, pattern='^to_start$'),
                CallbackQueryHandler(group_detail, pattern='^v_'),
                CallbackQueryHandler(bc_select, pattern='^start_bc$'),
                CallbackQueryHandler(lambda u,c: (u.callback_query.answer("數據已刷新") if sync_from_github() else u.callback_query.answer("刷新失敗")) or start(u,c), pattern='^sync_now$'),
            ],
            BCAST_GROUP: [
                CallbackQueryHandler(bc_get_msg, pattern='^bcg_'),
                CallbackQueryHandler(bc_temp_prompt, pattern='^bc_temp$')
            ],
            BCAST_TEMP_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bc_temp_save)
            ],
            BCAST_MSG: [
                MessageHandler(filters.ALL & ~filters.COMMAND, bc_do)
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
