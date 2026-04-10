import os, asyncio, sqlite3, logging
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# --- 配置區 ---
ADMIN_IDS = [5558898787,7549117882,6914258528,7309768391,7156620562] 
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 狀態定義 (修复：增加了分组和ID录入的状态)
(CHOOSING_ACTION, TYPING_GROUP_NAME, SELECT_GROUP_FOR_ADD, 
 TYPING_CHAT_ID, TYPING_BROADCAST_CONTENT) = range(5)

# --- 數據庫初始化 ---
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS groups (group_name TEXT, chat_id TEXT, UNIQUE(group_name, chat_id))')
    cursor.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)')
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
def run_web(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

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
    
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup)
    return CHOOSING_ACTION

# --- 分組管理功能 (修复：增加了具体动作处理) ---
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

# 添加新分组名
async def req_new_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("請輸入新分組的名稱：")
    return TYPING_GROUP_NAME

async def save_new_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text
    context.user_data['temp_group'] = name
    await update.message.reply_text(f"分組【{name}】已預設。現在請直接輸入群 ID，或轉發一條該群的消息給我：")
    return TYPING_CHAT_ID

# 为现有分组加群
async def req_select_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    groups = get_groups()
    if not groups:
        await update.callback_query.edit_message_text("❌ 目前沒有分組，請先創建。")
        return await manage_groups(update, context)
    
    keyboard = [[InlineKeyboardButton(g, callback_data=f"sel_{g}")] for g in groups.keys()]
    await update.callback_query.edit_message_text("請選擇要加入成員的分組：", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_GROUP_FOR_ADD

async def save_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 自动识别：如果是转发的消息取 ID，否则取文字
    cid = update.message.forward_from_chat.id if update.message.forward_from_chat else update.message.text
    group_name = context.user_data.get('temp_group')
    
    conn = sqlite3.connect('bot_data.db')
    conn.execute("INSERT OR IGNORE INTO groups (group_name, chat_id) VALUES (?, ?)", (group_name, str(cid)))
    conn.commit()
    conn.close()
    
    await update.message.reply_text(f"✅ 已成功將群 {cid} 加入分組 {group_name}")
    return await start(update, context)

# --- 群發邏輯 ---
async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    groups = get_groups()
    if not groups:
        await query.edit_message_text("❌ 尚未創建任何分組。")
        return await start(update, context)

    # 记忆逻辑
    conn = sqlite3.connect('bot_data.db')
    last_g = conn.execute("SELECT value FROM config WHERE key='last_group'").fetchone()
    conn.close()
    
    keyboard = []
    if last_g and last_g[0] in groups:
        keyboard.append([InlineKeyboardButton(f"⭐ 上次：{last_g[0]}", callback_data=f"group_{last_g[0]}")])
    for g in groups.keys():
        if last_g and g == last_g[0]: continue
        keyboard.append([InlineKeyboardButton(g, callback_data=f"group_{g}")])
    keyboard.append([InlineKeyboardButton("⬅️ 返回", callback_data='back_to_main')])
    
    await query.edit_message_text("請選擇要群發的分組：", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_ACTION

async def group_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    g_name = query.data.replace("group_", "")
    context.user_data['target_group'] = g_name
    
    conn = sqlite3.connect('bot_data.db')
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('last_group', ?)", (g_name,))
    conn.commit()
    conn.close()

    await query.edit_message_text(f"已選：【{g_name}】\n請輸入群發內容：")
    return TYPING_BROADCAST_CONTENT

async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text
    group_name = context.user_data.get('target_group')
    groups = get_groups()
    ids = groups.get(group_name, [])

    status = await update.message.reply_text(f"🚀 正在併發發送至 {len(ids)} 個群組...")
    
    async def send_task(cid):
        try:
            await context.bot.send_message(chat_id=cid, text=content)
            return True
        except: return False

    results = await asyncio.gather(*(send_task(cid) for cid in ids))
    await status.edit_text(f"✅ 群發完畢！成功: {results.count(True)}, 失敗: {results.count(False)}")
    return await start(update, context)

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
                CallbackQueryHandler(req_new_group, pattern='^add_g$'),
                CallbackQueryHandler(req_select_group, pattern='^add_c$'),
                CallbackQueryHandler(group_selected, pattern='^group_'),
            ],
            TYPING_GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_group)],
            SELECT_GROUP_FOR_ADD: [CallbackQueryHandler(lambda u,c: (c.user_data.update({'temp_group': u.callback_query.data.replace("sel_","")}), u.callback_query.edit_message_text("請發送群ID或轉發群消息："))[1] or TYPING_CHAT_ID, pattern='^sel_')],
            TYPING_CHAT_ID: [MessageHandler(filters.ALL & ~filters.COMMAND, save_chat_id)],
            TYPING_BROADCAST_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, execute_broadcast)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app_tg.add_handler(conv_handler)
    app_tg.run_polling()

if __name__ == '__main__':
    main()
