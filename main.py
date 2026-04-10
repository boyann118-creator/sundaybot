import os, asyncio, sqlite3
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# --- 基礎配置 ---
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562]
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 對話狀態定義
MENU, ADD_G_NAME, ADD_ID, ADD_REMARK, RENAME_G = range(5)

# --- 數據庫邏輯 ---
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS group_names (name TEXT PRIMARY KEY)')
        conn.execute('CREATE TABLE IF NOT EXISTS groups (group_name TEXT, chat_id TEXT, remark TEXT, UNIQUE(group_name, chat_id))')

def db_query(sql, params=()):
    with sqlite3.connect('bot_data.db') as conn:
        return conn.execute(sql, params).fetchall()

def db_exec(sql, params=()):
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute(sql, params)

# --- Web 伺服器 (保持 Render 喚醒) ---
app = Flask('')
@app.route('/')
def home(): return "Bot Active"
def run_web(): app.run(host='0.0.0.0', port=8080)

# --- 核心邏輯 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    kb = [[InlineKeyboardButton("📁 分組管理", callback_data='manage')],
          [InlineKeyboardButton("🚀 執行群發", callback_data='broadcast_entry')]]
    text = "🤖 **群發管理後台**\n請選擇操作項目："
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MENU

# 分組列表
async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    names = db_query("SELECT name FROM group_names")
    kb = [[InlineKeyboardButton(f"📂 {n[0]}", callback_data=f"v_{n[0]}")] for n in names]
    kb.append([InlineKeyboardButton("➕ 創建新分組", callback_data='new_g')])
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='back_main')])
    await query.edit_message_text("🗂️ **當前分組列表**：", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MENU

# 分組詳情 (這是你之前最缺的功能)
async def group_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    g_name = query.data.replace("v_", "")
    context.user_data['active_g'] = g_name
    members = db_query("SELECT chat_id, remark FROM groups WHERE group_name=?", (g_name,))
    
    text = f"📂 **分組名：{g_name}**\n\n成員清單："
    kb = []
    if not members:
        text += "\n(暫無成員)"
    else:
        for cid, remark in members:
            text += f"\n• {remark} (`{cid}`)"
            kb.append([InlineKeyboardButton(f"🗑️ 移除 {remark}", callback_data=f"del_m_{cid}")])
    
    kb.append([InlineKeyboardButton("➕ 添加群組", callback_data='add_m')])
    kb.append([InlineKeyboardButton("📝 修改分組名", callback_data='rename_g')])
    kb.append([InlineKeyboardButton("🔥 刪除此分組", callback_data='kill_g')])
    kb.append([InlineKeyboardButton("⬅️ 返回列表", callback_data='manage')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MENU

# --- 添加群組對話流 ---
async def add_m_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text(f"👉 請輸入群組 **ID** (必須帶 -)：\n分組：{context.user_data['active_g']}")
    return ADD_ID

async def add_m_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.message.text.strip()
    if not cid.startswith('-'):
        await update.message.reply_text("❌ ID 格式錯誤，必須以 - 開頭！請重新輸入：")
        return ADD_ID
    context.user_data['temp_cid'] = cid
    await update.message.reply_text(f"✅ 已記錄 ID: `{cid}`\n\n**現在請輸入該群的「備註名稱」：**", parse_mode="Markdown")
    return ADD_REMARK

async def add_m_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remark = update.message.text.strip()
    g_name = context.user_data['active_g']
    cid = context.user_data['temp_cid']
    db_exec("INSERT OR REPLACE INTO groups (group_name, chat_id, remark) VALUES (?, ?, ?)", (g_name, cid, remark))
    await update.message.reply_text(f"✨ 成功添加！\n分組：{g_name}\n群名：{remark}")
    return await start(update, context)

# --- 啟動 ---
def main():
    init_db()
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                CallbackQueryHandler(list_groups, pattern='^manage$'),
                CallbackQueryHandler(start, pattern='^back_main$'),
                CallbackQueryHandler(group_detail, pattern='^v_'),
                CallbackQueryHandler(add_m_start, pattern='^add_m$'),
                CallbackQueryHandler(lambda u,c: u.callback_query.edit_message_text("請輸入新分組名：") or ADD_G_NAME, pattern='^new_g$'),
                CallbackQueryHandler(lambda u,c: u.callback_query.edit_message_text("請輸入新的分組名稱：") or RENAME_G, pattern='^rename_g$'),
                CallbackQueryHandler(lambda u,c: (db_exec("DELETE FROM groups WHERE chat_id=?", (u.callback_query.data.replace("del_m_",""),))) or group_detail(u,c), pattern='^del_m_'),
                CallbackQueryHandler(lambda u,c: (db_exec("DELETE FROM group_names WHERE name=?", (c.user_data['active_g'],)), db_exec("DELETE FROM groups WHERE group_name=?", (c.user_data['active_g'],))) or list_groups(u,c), pattern='^kill_g$'),
            ],
            ADD_G_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: (db_exec("INSERT INTO group_names VALUES (?)", (u.message.text.strip(),))) or start(u,c))],
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_m_id)],
            ADD_REMARK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_m_final)],
            RENAME_G: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: (db_exec("UPDATE groups SET group_name=? WHERE group_name=?", (u.message.text.strip(), c.user_data['active_g'])), db_exec("INSERT INTO group_names VALUES (?)", (u.message.text.strip(),)), db_exec("DELETE FROM group_names WHERE name=?", (c.user_data['active_g'],))) or start(u,c))],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
