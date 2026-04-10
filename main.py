import os, asyncio, sqlite3, logging
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

# 定義流程狀態
(MAIN_STATE, ADD_GNAME, ADD_ID, ADD_REMARK, RENAME_G) = range(5)

# --- 2. 數據庫 ---
def db_op(sql, params=(), is_select=False):
    with sqlite3.connect('bot_data.db') as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        if is_select: return cur.fetchall()
        conn.commit()

def init_db():
    db_op('CREATE TABLE IF NOT EXISTS group_names (name TEXT PRIMARY KEY)')
    db_op('CREATE TABLE IF NOT EXISTS members (g_name TEXT, chat_id TEXT, remark TEXT, UNIQUE(g_name, chat_id))')

# --- 3. 核心功能函數 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    context.user_data.clear()
    kb = [[InlineKeyboardButton("📁 分組管理", callback_data='list_g')],
          [InlineKeyboardButton("🚀 執行群發", callback_data='start_bc')]]
    text = "🔘 **群發系統控制台**\n請選擇功能："
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

# 分組列表
async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    rows = db_op("SELECT name FROM group_names", is_select=True)
    kb = [[InlineKeyboardButton(f"📂 {r['name']}", callback_data=f"v_{r['name']}")] for r in rows]
    kb.append([InlineKeyboardButton("➕ 創建新分組", callback_data='create_g')])
    kb.append([InlineKeyboardButton("⬅️ 返回主選單", callback_data='to_start')])
    await update.callback_query.edit_message_text("🗂️ **分組列表**：", reply_markup=InlineKeyboardMarkup(kb))
    return MAIN_STATE

# 創建分組 - 第一步
async def create_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("✍️ 請發送 **新分組的名稱**：")
    return ADD_GNAME

# 創建分組 - 第二步（保存）
async def save_new_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    db_op("INSERT OR IGNORE INTO group_names VALUES (?)", (name,))
    await update.message.reply_text(f"✅ 分組「{name}」已創建。")
    return await start(update, context) # 強制返回主選單

# 分組詳情
async def group_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    g_name = query.data.replace("v_", "") if "v_" in query.data else context.user_data.get('active_g')
    context.user_data['active_g'] = g_name
    
    mems = db_op("SELECT chat_id, remark FROM members WHERE g_name=?", (g_name,), is_select=True)
    text = f"📂 **分組：{g_name}**\n\n成員清單："
    kb = []
    for m in mems:
        text += f"\n• {m['remark']} (`{m['chat_id']}`)"
        kb.append([InlineKeyboardButton(f"🗑️ 移除 {m['remark']}", callback_data=f"rm_{m['chat_id']}")])
    
    kb.append([InlineKeyboardButton("➕ 添加群組", callback_data='add_m_flow')])
    kb.append([InlineKeyboardButton("📝 修改分組名", callback_data='ren_g_flow')])
    kb.append([InlineKeyboardButton("🔥 刪除分組", callback_data='del_g_all')])
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='list_g')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

# 添加成員流程
async def add_mem_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"👉 **分組：{context.user_data['active_g']}**\n請發送群組 **ID**：")
    return ADD_ID

async def add_mem_remark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.message.text.strip()
    if not cid.startswith('-'):
        await update.message.reply_text("❌ ID 必須以 - 開頭，請重新輸入：")
        return ADD_ID
    context.user_data['temp_cid'] = cid
    await update.message.reply_text(f"✅ ID 已記錄。\n**請發送該群的備註名稱：**")
    return ADD_REMARK

async def add_mem_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remark = update.message.text.strip()
    db_op("INSERT OR REPLACE INTO members VALUES (?, ?, ?)", 
          (context.user_data['active_g'], context.user_data['temp_cid'], remark))
    await update.message.reply_text(f"✨ 已保存：{remark}")
    return await start(update, context)

# 刪除單個成員
async def remove_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.callback_query.data.replace("rm_", "")
    db_op("DELETE FROM members WHERE chat_id=? AND g_name=?", (cid, context.user_data['active_g']))
    await update.callback_query.answer("已移除")
    return await group_detail(update, context)

# 修改分組名
async def rename_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"✍️ 請輸入分組「{context.user_data['active_g']}」的新名稱：")
    return RENAME_G

async def rename_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_n = update.message.text.strip()
    old_n = context.user_data['active_g']
    db_op("INSERT OR IGNORE INTO group_names VALUES (?)", (new_n,))
    db_op("UPDATE members SET g_name=? WHERE g_name=?", (new_n, old_n))
    db_op("DELETE FROM group_names WHERE name=?", (old_n,))
    await update.message.reply_text(f"✅ 已改名為 {new_n}")
    return await start(update, context)

# --- 4. 啟動 ---
app = Flask('')
@app.route('/')
def home(): return "Bot Active"
def run_web(): app.run(host='0.0.0.0', port=8080)

def main():
    init_db()
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [
                CallbackQueryHandler(list_groups, pattern='^list_g$'),
                CallbackQueryHandler(start, pattern='^to_start$'),
                CallbackQueryHandler(group_detail, pattern='^v_'),
                CallbackQueryHandler(create_group_start, pattern='^create_g$'),
                CallbackQueryHandler(add_mem_id, pattern='^add_m_flow$'),
                CallbackQueryHandler(rename_start, pattern='^ren_g_flow$'),
                CallbackQueryHandler(remove_member, pattern='^rm_'),
                CallbackQueryHandler(lambda u,c: (db_op("DELETE FROM group_names WHERE name=?", (c.user_data['active_g'],)), db_op("DELETE FROM members WHERE g_name=?", (c.user_data['active_g'],))) or list_groups(u,c), pattern='^del_g_all$'),
            ],
            ADD_GNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_group)],
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mem_remark)],
            ADD_REMARK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mem_save)],
            RENAME_G: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_save)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
