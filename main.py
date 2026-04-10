import os, asyncio, sqlite3, logging
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# --- 配置區 ---
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562] 
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 狀態定義
(CHOOSING_ACTION, TYPING_GROUP_NAME, SELECT_GROUP_FOR_ADD, 
 TYPING_CHAT_ID, TYPING_BROADCAST_CONTENT) = range(5)

# --- 數據庫邏輯 ---
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS group_names (name TEXT PRIMARY KEY)')
        # 增加 title 字段來存儲自動抓取的群組名稱
        conn.execute('CREATE TABLE IF NOT EXISTS groups (group_name TEXT, chat_id TEXT, title TEXT, UNIQUE(group_name, chat_id))')

def get_all_group_names():
    with sqlite3.connect('bot_data.db') as conn:
        return [row[0] for row in conn.execute("SELECT name FROM group_names").fetchall()]

def get_group_details():
    with sqlite3.connect('bot_data.db') as conn:
        cursor = conn.execute("SELECT group_name, chat_id, title FROM groups")
        data = {}
        for g_name, cid, title in cursor.fetchall():
            if g_name not in data: data[g_name] = []
            data[g_name].append({"id": cid, "title": title or "未知群組"})
        return data

# --- Web 伺服器 ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Alive!"
def run_web(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- 機器人邏輯 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    keyboard = [[InlineKeyboardButton("📁 分組管理", callback_data='manage')],
                [InlineKeyboardButton("🚀 開始群發", callback_data='broadcast_main')]]
    msg = "✅ 系統就緒。請選擇操作：\n(提示：發送 /start 可隨時重置)"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_ACTION

async def manage_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    names = get_all_group_names()
    details = get_group_details()
    
    info = "📊 目前分組詳情：\n"
    if not names: info += "（暫無分組）"
    
    keyboard = []
    for n in names:
        members = details.get(n, [])
        info += f"\n📂 **{n}** ({len(members)}個):\n"
        for m in members:
            info += f" └ {m['title']} (`{m['id']}`)\n"
        keyboard.append([InlineKeyboardButton(f"⚙️ 管理 {n}", callback_data=f"opt_{n}")])
    
    keyboard.append([InlineKeyboardButton("➕ 創建新分組", callback_data='add_g')])
    keyboard.append([InlineKeyboardButton("⬅️ 返回", callback_data='back_to_main')])
    await query.edit_message_text(info + "\n請選擇操作：", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_ACTION

async def group_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = update.callback_query.data.replace("opt_", "")
    context.user_data['temp_group'] = g_name
    keyboard = [
        [InlineKeyboardButton("➕ 往此分組加群", callback_data=f"sel_{g_name}")],
        [InlineKeyboardButton("🗑️ 刪除整個分組", callback_data=f"del_{g_name}")],
        [InlineKeyboardButton("⬅️ 返回", callback_data='manage')]
    ]
    await update.callback_query.edit_message_text(f"正在管理分組：【{g_name}】", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_ACTION

async def delete_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = update.callback_query.data.replace("del_", "")
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute("DELETE FROM group_names WHERE name=?", (g_name,))
        conn.execute("DELETE FROM groups WHERE group_name=?", (g_name,))
    await update.callback_query.answer(f"已刪除分組 {g_name}")
    return await manage_menu(update, context)

async def req_new_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("請輸入新分組的名稱：")
    return TYPING_GROUP_NAME

async def save_new_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute("INSERT OR IGNORE INTO group_names (name) VALUES (?)", (name,))
    context.user_data['temp_group'] = name
    await update.message.reply_text(f"✅ 分組【{name}】已建立。\n\n請發送 **群 ID** (帶-) 或直接 **轉發消息** 給我：")
    return TYPING_CHAT_ID

# --- 核心：自動獲取群名並存儲 ---
async def save_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    g_name = context.user_data.get('temp_group')
    
    if msg.forward_from_chat:
        cid = str(msg.forward_from_chat.id)
    else:
        cid = msg.text.strip() if msg.text else ""

    if not cid.startswith('-'):
        await msg.reply_text("❌ 格式錯誤！ID 必須以 `-` 開頭。")
        return TYPING_CHAT_ID

    status_msg = await msg.reply_text(f"⏳ 正在聯網識別群組 `{cid}` ...")
    
    try:
        # 嘗試從 Telegram 伺服器獲取該群組的名稱
        chat = await context.bot.get_chat(cid)
        title = chat.title or "私人群組"
    except Exception:
        title = "無法獲取群名(機器人不在群內?)"

    try:
        with sqlite3.connect('bot_data.db') as conn:
            conn.execute("INSERT OR REPLACE INTO groups (group_name, chat_id, title) VALUES (?, ?, ?)", (g_name, cid, title))
        await status_msg.edit_text(f"✅ **存儲成功！**\n群名：`{title}`\nID：`{cid}`\n分組：【{g_name}】\n\n可繼續發送下一個 ID。")
    except Exception as e:
        await status_msg.edit_text(f"❌ 存儲失敗：{e}")
    
    return TYPING_CHAT_ID

# --- 群發邏輯 ---
async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    names = get_all_group_names()
    if not names:
        await update.callback_query.answer("❌ 尚無分組", show_alert=True)
        return
    keyboard = [[InlineKeyboardButton(n, callback_data=f"send_{n}")] for n in names]
    keyboard.append([InlineKeyboardButton("⬅️ 返回", callback_data='back_to_main')])
    await update.callback_query.edit_message_text("🎯 請選擇目標分組：", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_ACTION

async def req_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['target_group'] = update.callback_query.data.replace("send_", "")
    await update.callback_query.edit_message_text(f"已選：【{context.user_data['target_group']}】\n請輸入群發內容：")
    return TYPING_BROADCAST_CONTENT

async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text
    g_name = context.user_data.get('target_group')
    members = get_group_details().get(g_name, [])
    
    status_msg = await update.message.reply_text(f"🚀 開始發送至【{g_name}】...")
    success = 0
    for m in members:
        try:
            await context.bot.send_message(chat_id=m['id'], text=content)
            success += 1
            await asyncio.sleep(0.05)
        except: pass
    
    await status_msg.edit_text(f"🏁 群發完畢！\n分組：{g_name}\n成功: {success}\n失敗: {len(members)-success}")
    return await start(update, context)

# --- 主入口 ---
def main():
    init_db()
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    
    handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_ACTION: [
                CallbackQueryHandler(manage_menu, pattern='^manage$'),
                CallbackQueryHandler(start_broadcast, pattern='^broadcast_main$'),
                CallbackQueryHandler(start, pattern='^back_to_main$'),
                CallbackQueryHandler(req_new_group, pattern='^add_g$'),
                CallbackQueryHandler(group_options, pattern='^opt_'),
                CallbackQueryHandler(delete_group, pattern='^del_'),
                CallbackQueryHandler(req_content, pattern='^send_'),
                CallbackQueryHandler(lambda u,c: (c.user_data.update({'temp_group': u.callback_query.data.replace("sel_","")}), u.callback_query.edit_message_text("請發送 ID："))[1] or TYPING_CHAT_ID, pattern='^sel_'),
            ],
            TYPING_GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_group)],
            TYPING_CHAT_ID: [MessageHandler(filters.ALL & ~filters.COMMAND, save_chat_id)],
            TYPING_BROADCAST_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, do_broadcast)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    app_tg.add_handler(handler)
    app_tg.run_polling()

if __name__ == '__main__': main()
