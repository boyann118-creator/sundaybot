import os, asyncio, sqlite3, logging
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# --- 基礎配置 ---
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562] 
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 定義對話狀態 (防止機器人沒反應的核心)
(CHOOSING_ACTION, TYPING_GROUP_NAME, TYPING_CHAT_ID, 
 TYPING_REMARK_NAME, TYPING_BROADCAST_CONTENT, TYPING_NEW_GNAME) = range(6)

# --- 數據庫初始化 ---
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS group_names (name TEXT PRIMARY KEY)')
        conn.execute('CREATE TABLE IF NOT EXISTS groups (group_name TEXT, chat_id TEXT, title TEXT, UNIQUE(group_name, chat_id))')

def db_query(sql, params=()):
    with sqlite3.connect('bot_data.db') as conn:
        return conn.execute(sql, params).fetchall()

def db_exec(sql, params=()):
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute(sql, params)

# --- Web 存活檢查 ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Alive!"
def run_web(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- 主菜單 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    keyboard = [
        [InlineKeyboardButton("📁 分組管理", callback_data='manage')],
        [InlineKeyboardButton("🚀 開始群發", callback_data='broadcast_main')]
    ]
    text = "📌 **機器人管理後台**\n請選擇您要執行的操作："
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return CHOOSING_ACTION

# --- 分組管理介面 (支持修改、刪除、查看) ---
async def manage_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    names = [row[0] for row in db_query("SELECT name FROM group_names")]
    
    keyboard = [[InlineKeyboardButton(f"📂 {n}", callback_data=f"view_{n}")] for n in names]
    keyboard.append([InlineKeyboardButton("➕ 創建新分組", callback_data='add_g')])
    keyboard.append([InlineKeyboardButton("⬅️ 返回主選單", callback_data='back_to_main')])
    
    await query.edit_message_text("🗂️ **分組列表**\n點擊分組名稱可進入管理成員、修改名稱或刪除分組：", 
                                 reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return CHOOSING_ACTION

# 查看分組詳情 (管理群組成員)
async def view_group_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    g_name = query.data.replace("view_", "")
    context.user_data['temp_group'] = g_name
    
    members = db_query("SELECT chat_id, title FROM groups WHERE group_name=?", (g_name,))
    
    text = f"📂 **分組：{g_name}**\n\n成員列表："
    keyboard = []
    if not members:
        text += "\n(目前沒有群組)"
    else:
        for cid, title in members:
            text += f"\n• {title} (`{cid}`)"
            # 為每個群組生成一個單獨的刪除按鈕
            keyboard.append([InlineKeyboardButton(f"🗑️ 刪除 {title}", callback_data=f"rm_mem_{cid}")])
    
    keyboard.append([InlineKeyboardButton("➕ 添加群組 (ID)", callback_data=f"sel_{g_name}")])
    keyboard.append([InlineKeyboardButton("📝 修改此分組名稱", callback_data=f"rename_{g_name}")])
    keyboard.append([InlineKeyboardButton("🔥 刪除整個分組", callback_data=f"del_g_{g_name}")])
    keyboard.append([InlineKeyboardButton("⬅️ 返回列表", callback_data='manage')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return CHOOSING_ACTION

# --- 添加群組 ID 與 備註名 (關鍵改進) ---
async def req_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = update.callback_query.data.replace("sel_", "")
    context.user_data['temp_group'] = g_name
    await update.callback_query.edit_message_text(f"🔹 分組：【{g_name}】\n請輸入要添加的 **群組 ID** (必須以 - 開頭)：")
    return TYPING_CHAT_ID

async def req_remark_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.message.text.strip()
    if not cid.startswith('-'):
        await update.message.reply_text("❌ 格式不正確！群 ID 必須以 `-` 開頭，請重新輸入：")
        return TYPING_CHAT_ID
    
    context.user_data['temp_cid'] = cid
    await update.message.reply_text(f"📍 ID `{cid}` 已記錄。\n**請現在輸入該群的備註名稱** (例如：虎總蘭德內部群)：", parse_mode="Markdown")
    return TYPING_REMARK_NAME

async def save_group_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    g_name = context.user_data.get('temp_group')
    cid = context.user_data.get('temp_cid')
    
    db_exec("INSERT OR REPLACE INTO groups (group_name, chat_id, title) VALUES (?, ?, ?)", (g_name, cid, title))
    await update.message.reply_text(f"✅ **保存成功！**\n群名：{title}\nID：{cid}\n分組：{g_name}\n\n您可以繼續輸入下一個 **群組 ID**，或輸入 /start 返回菜單。")
    return TYPING_CHAT_ID

# --- 刪除群組與改名功能 ---
async def delete_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.callback_query.data.replace("rm_mem_", "")
    g_name = context.user_data.get('temp_group')
    db_exec("DELETE FROM groups WHERE chat_id=? AND group_name=?", (cid, g_name))
    await update.callback_query.answer(f"已刪除成員")
    return await view_group_detail(update, context)

async def rename_group_req(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("請輸入該分組的 **新名稱**：")
    return TYPING_NEW_GNAME

async def rename_group_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    old_name = context.user_data.get('temp_group')
    db_exec("INSERT OR IGNORE INTO group_names (name) VALUES (?)", (new_name,))
    db_exec("UPDATE groups SET group_name=? WHERE group_name=?", (new_name, old_name))
    db_exec("DELETE FROM group_names WHERE name=?", (old_name,))
    await update.message.reply_text(f"✅ 改名成功！從 {old_name} 改為 {new_name}")
    return await start(update, context)

# --- 基礎函數 (創建分組、群發等) ---
async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text
    g_name = context.user_data.get('target_group')
    members = db_query("SELECT chat_id FROM groups WHERE group_name=?", (g_name,))
    
    msg = await update.message.reply_text(f"🚀 正在發送至【{g_name}】...")
    success = 0
    for (cid,) in members:
        try:
            await context.bot.send_message(chat_id=cid, text=content)
            success += 1
            await asyncio.sleep(0.05)
        except: pass
    await msg.edit_text(f"🏁 發送完畢！成功: {success}, 失敗: {len(members)-success}")
    return await start(update, context)

# --- 啟動入口 ---
def main():
    init_db()
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_ACTION: [
                CallbackQueryHandler(manage_menu, pattern='^manage$'),
                CallbackQueryHandler(start, pattern='^back_to_main$'),
                CallbackQueryHandler(view_group_detail, pattern='^view_'),
                CallbackQueryHandler(lambda u,c: u.callback_query.edit_message_text("請輸入新分組名稱：") or TYPING_GROUP_NAME, pattern='^add_g$'),
                CallbackQueryHandler(req_chat_id, pattern='^sel_'),
                CallbackQueryHandler(delete_member, pattern='^rm_mem_'),
                CallbackQueryHandler(rename_group_req, pattern='^rename_'),
                CallbackQueryHandler(lambda u,c: (db_exec("DELETE FROM group_names WHERE name=?", (u.callback_query.data.replace("del_g_",""),)), db_exec("DELETE FROM groups WHERE group_name=?", (u.callback_query.data.replace("del_g_",""),))) or manage_menu(u,c), pattern='^del_g_'),
                CallbackQueryHandler(lambda u,c: (names := db_query("SELECT name FROM group_names"), u.callback_query.edit_message_text("🎯 選擇目標分組：", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(n[0], callback_data=f"send_{n[0]}") for n in names]])))[1] or CHOOSING_ACTION, pattern='^broadcast_main$'),
                CallbackQueryHandler(lambda u,c: (c.user_data.update({'target_group': u.callback_query.data.replace("send_","")}), u.callback_query.edit_message_text(f"請輸入群發內容："))[1] or TYPING_BROADCAST_CONTENT, pattern='^send_'),
            ],
            TYPING_GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: (db_exec("INSERT OR IGNORE INTO group_names (name) VALUES (?)", (u.message.text.strip(),)), u.message.reply_text("✅ 分組已創建！"))[1] or start(u,c))],
            TYPING_CHAT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_remark_name)],
            TYPING_REMARK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_group_info)],
            TYPING_BROADCAST_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, do_broadcast)],
            TYPING_NEW_GNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_group_done)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
