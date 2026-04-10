import os, asyncio, sqlite3, logging
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# --- 配置區 ---
# 請確保 ADMIN_IDS 中的 ID 是正確的
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562] 
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 狀態定義
(CHOOSING_ACTION, TYPING_GROUP_NAME, SELECT_GROUP_FOR_ADD, 
 TYPING_CHAT_ID, TYPING_BROADCAST_CONTENT) = range(5)

# --- 數據庫邏輯 ---
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS group_names (name TEXT PRIMARY KEY)')
        conn.execute('CREATE TABLE IF NOT EXISTS groups (group_name TEXT, chat_id TEXT, UNIQUE(group_name, chat_id))')
        conn.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)')

def get_all_group_names():
    with sqlite3.connect('bot_data.db') as conn:
        return [row[0] for row in conn.execute("SELECT name FROM group_names").fetchall()]

def get_group_details():
    with sqlite3.connect('bot_data.db') as conn:
        cursor = conn.execute("SELECT group_name, chat_id FROM groups")
        data = {}
        for name, cid in cursor.fetchall():
            if name not in data: data[name] = []
            data[name].append(cid)
        return data

# --- Web 伺服器 (用於保持 Render 喚醒) ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Alive!"
def run_web(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- 機器人主邏輯 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    
    keyboard = [[InlineKeyboardButton("📁 分組管理", callback_data='manage')],
                [InlineKeyboardButton("🚀 開始群發", callback_data='broadcast_main')]]
    msg = "✅ 系統就緒。請選擇操作：\n(提示：隨時發送 /start 可重置狀態)"
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_ACTION

# --- 分組管理模塊 ---
async def manage_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    names = get_all_group_names()
    details = get_group_details()
    
    info = "📊 目前分組狀態：\n"
    if not names: info += "（暫無分組）"
    
    keyboard = []
    for n in names:
        count = len(details.get(n, []))
        info += f"• {n} ({count}個群組)\n"
        keyboard.append([InlineKeyboardButton(f"⚙️ 管理 {n}", callback_data=f"opt_{n}")])
    
    keyboard.append([InlineKeyboardButton("➕ 創建新分組", callback_data='add_g')])
    keyboard.append([InlineKeyboardButton("⬅️ 返回", callback_data='back_to_main')])
    await query.edit_message_text(info + "\n請選擇操作：", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_ACTION

async def group_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = update.callback_query.data.replace("opt_", "")
    context.user_data['temp_group'] = g_name
    
    keyboard = [
        [InlineKeyboardButton("➕ 往此分組加群/ID", callback_data=f"sel_{g_name}")],
        [InlineKeyboardButton("🗑️ 刪除整個分組", callback_data=f"del_{g_name}")],
        [InlineKeyboardButton("⬅️ 返回", callback_data='manage')]
    ]
    await update.callback_query.edit_message_text(f"正在管理分組：【{g_name}】\n請選擇操作：", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_ACTION

async def delete_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = update.callback_query.data.replace("del_", "")
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute("DELETE FROM group_names WHERE name=?", (g_name,))
        conn.execute("DELETE FROM groups WHERE group_name=?", (g_name,))
    await update.callback_query.answer(f"已刪除分組 {g_name}", show_alert=True)
    return await manage_menu(update, context)

# --- 創建與添加邏輯 ---
async def req_new_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("請輸入新分組名稱：")
    return TYPING_GROUP_NAME

async def save_new_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute("INSERT OR IGNORE INTO group_names (name) VALUES (?)", (name,))
    context.user_data['temp_group'] = name
    await update.message.reply_text(f"✅ 分組【{name}】已建立。\n\n現在請發送 **群組 ID** (必須帶 -，如 -100xxx) 或直接 **「轉發」** 一條該群的消息給我：")
    return TYPING_CHAT_ID

async def save_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    # 判斷是轉發的消息還是純文本
    if msg.forward_from_chat:
        cid = str(msg.forward_from_chat.id)
    elif msg.text:
        cid = msg.text.strip()
    else:
        return TYPING_CHAT_ID

    g_name = context.user_data.get('temp_group')
    
    # 強制驗證格式
    if not cid.startswith('-'):
        await msg.reply_text(f"⚠️ **格式錯誤！**\n您發送的是: `{cid}`\n群組 ID 必須以 '-' 開頭。請重新輸入：")
        return TYPING_CHAT_ID

    try:
        with sqlite3.connect('bot_data.db') as conn:
            conn.execute("INSERT OR IGNORE INTO groups (group_name, chat_id) VALUES (?, ?)", (g_name, cid))
        await msg.reply_text(f"✅ **成功存儲！**\nID: `{cid}` 已加入【{g_name}】\n\n您可以繼續發送下一個 ID，或點擊 /start 返回。")
    except Exception as e:
        await msg.reply_text(f"❌ 數據庫寫入失敗: {e}")
        
    return TYPING_CHAT_ID

# --- 群發邏輯 ---
async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    details = get_group_details()
    if not details:
        await update.callback_query.answer("❌ 沒有分組或組內無成員", show_alert=True)
        return
    
    keyboard = [[InlineKeyboardButton(g, callback_data=f"send_{g}")] for g in details.keys()]
    keyboard.append([InlineKeyboardButton("⬅️ 返回", callback_data='back_to_main')])
    await update.callback_query.edit_message_text("🎯 請選擇發送分組：", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_ACTION

async def req_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['target_group'] = update.callback_query.data.replace("send_", "")
    await update.callback_query.edit_message_text(f"已選：【{context.user_data['target_group']}】\n請輸入群發內容：")
    return TYPING_BROADCAST_CONTENT

async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text
    g_name = context.user_data.get('target_group')
    ids = get_group_details().get(g_name, [])
    
    status_msg = await update.message.reply_text(f"🚀 正在發送至 {len(ids)} 個群組...")
    
    success = 0
    for cid in ids:
        try:
            await context.bot.send_message(chat_id=cid, text=content)
            success += 1
            await asyncio.sleep(0.05) # 避開頻率限制
        except Exception:
            pass
    
    await status_msg.edit_text(f"🏁 發送完畢！\n分組: {g_name}\n成功: {success}\n失敗: {len(ids)-success}")
    return await start(update, context)

# --- 入口 ---
def main():
    init_db()
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
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
                # 處理從分組管理直接進入添加 ID 的快捷路徑
                CallbackQueryHandler(lambda u,c: (c.user_data.update({'temp_group': u.callback_query.data.replace("sel_","")}), u.callback_query.edit_message_text("請發送 ID："))[1] or TYPING_CHAT_ID, pattern='^sel_'),
            ],
            TYPING_GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_group)],
            TYPING_CHAT_ID: [MessageHandler(filters.ALL & ~filters.COMMAND, save_chat_id)],
            TYPING_BROADCAST_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, do_broadcast)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
