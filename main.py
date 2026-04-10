import os
import asyncio
import sqlite3
import logging
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# --- 配置區 ---
# 填入你和其它管理員的 Telegram User ID (整數)
ADMIN_IDS = [12345678, 87654321] 
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 狀態定義
(CHOOSING_ACTION, TYPING_GROUP_NAME, TYPING_CHAT_ID, 
 TYPING_BROADCAST_CONTENT, CONFIRM_SEND) = range(5)

# --- 數據庫初始化 ---
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS groups 
                      (group_name TEXT, chat_id TEXT, UNIQUE(group_name, chat_id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS config 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    conn.commit()
    conn.close()

def get_groups():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT group_name, chat_id FROM groups")
    data = {}
    for name, cid in cursor.fetchall():
        if name not in data: data[name] = []
        data[name].append(cid)
    conn.close()
    return data

# --- 防休眠 Web 伺服器 ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running!"

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- 機器人邏輯 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("📁 分組管理", callback_data='manage')],
        [InlineKeyboardButton("🚀 開始群發", callback_data='broadcast_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "歡迎使用群發機器人。請選擇操作："
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
    return CHOOSING_ACTION

# --- 群發邏輯 (併發) ---
async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    groups = get_groups()
    if not groups:
        await query.edit_message_text("❌ 尚未創建任何分組，請先前往分組管理。")
        return await start(update, context)

    # 讀取上次使用的分組
    conn = sqlite3.connect('bot_data.db')
    last_g = conn.execute("SELECT value FROM config WHERE key='last_group'").fetchone()
    conn.close()
    
    keyboard = []
    if last_g and last_g[0] in groups:
        keyboard.append([InlineKeyboardButton(f"⭐ 上次使用：{last_g[0]}", callback_data=f"group_{last_g[0]}")])
    
    for g_name in groups.keys():
        if last_g and g_name == last_g[0]: continue
        keyboard.append([InlineKeyboardButton(g_name, callback_data=f"group_{g_name}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ 返回", callback_data='back_to_main')])
    await query.edit_message_text("請選擇要發送到的分組：", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_ACTION

async def group_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    group_name = query.data.replace("group_", "")
    context.user_data['target_group'] = group_name
    
    # 記錄為上次使用
    conn = sqlite3.connect('bot_data.db')
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('last_group', ?)", (group_name,))
    conn.commit()
    conn.close()

    await query.edit_message_text(f"已選擇分組：【{group_name}】\n請輸入要群發的文字內容：")
    return TYPING_BROADCAST_CONTENT

async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text
    group_name = context.user_data.get('target_group')
    groups = get_groups()
    chat_ids = groups.get(group_name, [])

    status_msg = await update.message.reply_text(f"正在向 {len(chat_ids)} 個群組併發發送...")

    async def send_task(cid):
        try:
            await context.bot.send_message(chat_id=cid, text=content)
            return True
        except:
            return False

    # 核心：併發執行
    tasks = [send_task(cid) for cid in chat_ids]
    results = await asyncio.gather(*tasks)
    
    success = results.count(True)
    fail = results.count(False)
    
    await status_msg.edit_text(f"✅ 群發完成！\n成功：{success}\n失敗：{fail}")
    return await start(update, context)

# --- 分組管理邏輯 (簡化版) ---
async def manage_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("➕ 添加新分組", callback_data='add_g')],
        [InlineKeyboardButton("➕ 為分組添加群(ID)", callback_data='add_c')],
        [InlineKeyboardButton("⬅️ 返回", callback_data='back_to_main')]
    ]
    await query.edit_message_text("分組管理菜單：", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_ACTION

# --- 主函數 ---
def main():
    init_db()
    Thread(target=run_web).start()

    app_tg = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_ACTION: [
                CallbackQueryHandler(manage_groups, pattern='^manage$'),
                CallbackQueryHandler(start_broadcast, pattern='^broadcast_main$'),
                CallbackQueryHandler(start, pattern='^back_to_main$'),
                CallbackQueryHandler(group_selected, pattern='^group_'),
                # 此處可擴展 添加分組/添加群 的具體 Callback
            ],
            TYPING_BROADCAST_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, execute_broadcast)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app_tg.add_handler(conv_handler)
    app_tg.run_polling()

if __name__ == '__main__':
    main()
