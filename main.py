import os, asyncio, sqlite3
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# --- 基礎配置 ---
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562] 
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 定義全新狀態機
(STATE_MANAGE, STATE_NEW_GNAME, STATE_INPUT_ID, STATE_INPUT_REMARK, 
 STATE_BROADCAST_CONTENT, STATE_RENAME_GROUP) = range(6)

# --- 數據庫：全新優化表結構 ---
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS group_names (name TEXT PRIMARY KEY)')
        conn.execute('CREATE TABLE IF NOT EXISTS groups (group_name TEXT, chat_id TEXT, remark TEXT, UNIQUE(group_name, chat_id))')

def db_action(sql, params=()):
    with sqlite3.connect('bot_data.db') as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall()

# --- Web 存活檢查 ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Alive!"
def run_web(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- 界面工具 ---
def get_main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 分組管理", callback_data='cmd_manage')],
        [InlineKeyboardButton("🚀 執行群發", callback_data='cmd_broadcast')]
    ])

# --- 核心邏輯 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    msg = "👋 **您好！群發管理系統已重置。**\n\n請選擇操作："
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=get_main_kb(), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=get_main_kb(), parse_mode="Markdown")
    return STATE_MANAGE

# 1. 分組列表頁面
async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    names = db_action("SELECT name FROM group_names")
    
    kb = [[InlineKeyboardButton(f"📂 {n[0]}", callback_data=f"view_{n[0]}")] for n in names]
    kb.append([InlineKeyboardButton("➕ 創建新分組", callback_data='new_g')])
    kb.append([InlineKeyboardButton("⬅️ 返回主選單", callback_data='to_main')])
    
    await query.edit_message_text("🗂️ **所有分組：**", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return STATE_MANAGE

# 2. 單個分組詳情頁面 (支持刪除成員、改分組名)
async def view_group_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    g_name = query.data.replace("view_", "")
    context.user_data['active_g'] = g_name
    
    members = db_action("SELECT chat_id, remark FROM groups WHERE group_name=?", (g_name,))
    
    text = f"📂 **管理分組：{g_name}**\n\n群組成員："
    kb = []
    if not members:
        text += "\n(目前暫無成員)"
    else:
        for cid, remark in members:
            text += f"\n• {remark} (`{cid}`)"
            kb.append([InlineKeyboardButton(f"🗑️ 刪除 {remark}", callback_data=f"rm_{cid}")])
    
    kb.append([InlineKeyboardButton("➕ 添加群組(ID)", callback_data='add_mem')])
    kb.append([InlineKeyboardButton("📝 修改分組名稱", callback_data='rename_g')])
    kb.append([InlineKeyboardButton("🔥 徹底刪除分組", callback_data='kill_g')])
    kb.append([InlineKeyboardButton("⬅️ 返回列表", callback_data='cmd_manage')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return STATE_MANAGE

# 3. 添加群組流程 (ID -> 備註)
async def add_mem_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"👉 請發送要添加的 **群組 ID** (必須帶 -)：\n\n分組：{context.user_data['active_g']}")
    return STATE_INPUT_ID

async def add_mem_id_rcvd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.message.text.strip()
    if not cid.startswith('-'):
        await update.message.reply_text("❌ ID 格式不對（必須以 - 開頭），請重新輸入：")
        return STATE_INPUT_ID
    context.user_data['active_cid'] = cid
    await update.message.reply_text(f"✅ ID 已記錄：`{cid}`\n\n**現在請發送該群的「備註名稱」：**\n(例如：虎總蘭德內部群)", parse_mode="Markdown")
    return STATE_INPUT_REMARK

async def add_mem_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remark = update.message.text.strip()
    g_name = context.user_data['active_g']
    cid = context.user_data['active_cid']
    
    db_action("INSERT OR REPLACE INTO groups (group_name, chat_id, remark) VALUES (?, ?, ?)", (g_name, cid, remark))
    await update.message.reply_text(f"✨ 成功！【{remark}】已加入分組【{g_name}】。")
    # 這裡直接回到詳情頁面的邏輯
    return await start(update, context)

# 4. 修改分組名流程
async def rename_g_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text(f"請發送【{context.user_data['active_g']}】的新名稱：")
    return STATE_RENAME_GROUP

async def rename_g_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_n = update.message.text.strip()
    old_n = context.user_data['active_g']
    db_action("INSERT INTO group_names (name) VALUES (?)", (new_n,))
    db_action("UPDATE groups SET group_name=? WHERE group_name=?", (new_n, old_n))
    db_action("DELETE FROM group_names WHERE name=?", (old_n,))
    await update.message.reply_text(f"✅ 已改名為：{new_n}")
    return await start(update, context)

# --- 啟動入口 ---
def main():
    init_db()
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            STATE_MANAGE: [
                CallbackQueryHandler(list_groups, pattern='^cmd_manage$'),
                CallbackQueryHandler(start, pattern='^to_main$'),
                CallbackQueryHandler(view_group_detail, pattern='^view_'),
                CallbackQueryHandler(add_mem_start, pattern='^add_mem$'),
                CallbackQueryHandler(rename_g_start, pattern='^rename_g$'),
                CallbackQueryHandler(lambda u,c: (db_action("DELETE FROM group_names WHERE name=?", (c.user_data['active_g'],)), db_action("DELETE FROM groups WHERE group_name=?", (c.user_data['active_g'],)))[1] or list_groups(u,c), pattern='^kill_g$'),
                CallbackQueryHandler(lambda u,c: (db_action("DELETE FROM groups WHERE chat_id=? AND group_name=?", (u.callback_query.data.replace("rm_",""), c.user_data['active_g'])))[1] or view_group_detail(u,c), pattern='^rm_'),
                CallbackQueryHandler(lambda u,c: u.callback_query.edit_message_text("請輸入新分組名稱：") or STATE_NEW_GNAME, pattern='^new_g$'),
            ],
            STATE_NEW_GNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: db_action("INSERT OR IGNORE INTO group_names (name) VALUES (?)", (u.message.text.strip(),)) or start(u,c))],
            STATE_INPUT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mem_id_rcvd)],
            STATE_INPUT_REMARK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mem_final)],
            STATE_RENAME_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_g_done)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
