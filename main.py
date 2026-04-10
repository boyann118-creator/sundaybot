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
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562]
TOKEN = os.getenv("TELEGRAM_TOKEN")
GITHUB_RAW_URL = "https://raw.githubusercontent.com/boyann118-creator/sundaybot/refs/heads/main/groups.json"

(MAIN_STATE, BCAST_GROUP, BCAST_MSG) = range(3)

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
    
    # --- 重點修改部分：只顯示 remark (名稱)，不再顯示 chat_id ---
    text = f"📂 **分組：{g_name}**\n\n成員清單："
    if not mems:
        text += "\n(空)"
    else:
        for m in mems:
            text += f"\n• {m['remark']}" # 這裡刪掉了原本顯示 ID 的部分
    
    kb = [[InlineKeyboardButton("⬅️ 返回列表", callback_data='list_g')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

# --- 群發流程 ---

async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    groups = DATA_CACHE.get("groups", [])
    if not groups:
        await update.callback_query.edit_message_text("❌ 數據庫為空。")
        return MAIN_STATE
    kb = [[InlineKeyboardButton(f"🚀 發送至：{name}", callback_data=f"bcg_{name}")] for name in groups]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    await update.callback_query.edit_message_text("📢 **選擇群發目標分組：**", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def bc_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = update.callback_query.data.replace("bcg_", "")
    context.user_data['bc_target'] = g_name
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"📝 **目標分組：{g_name}**\n\n請發送群發內容：")
    return BCAST_MSG

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = context.user_data.get('bc_target')
    mems = [m for m in DATA_CACHE.get("members", []) if m['g_name'] == g_name]
    success = 0
    status_msg = await update.message.reply_text("⏳ 正在群發中...")
    for m in mems:
        try:
            await context.bot.copy_message(chat_id=m['chat_id'], from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            success += 1
        except: continue
    await status_msg.edit_text(f"✅ **發送任務完成！**\n\n📍 分組：{g_name}\n🎉 成功：{success}/{len(mems)}\n\n**可繼續發送內容，或輸入 /start 返回。**")
    return BCAST_MSG

# --- 啟動與 Web 服務 ---
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
            BCAST_GROUP: [CallbackQueryHandler(bc_get_msg, pattern='^bcg_')],
            BCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, bc_do)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
