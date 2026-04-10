import os, asyncio, sqlite3, logging
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# --- 1. 基礎配置與日誌 ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562]
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 定義流程狀態
(MAIN_STATE, ADD_GNAME, ADD_ID, ADD_REMARK, RENAME_G, BCAST_MSG) = range(6)

# --- 2. 數據庫架構 (增加原子化操作) ---
def db_op(sql, params=(), is_select=False):
    with sqlite3.connect('bot_data.db') as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        if is_select: return cur.fetchall()
        conn.commit()

def init_db():
    db_op('CREATE TABLE IF NOT EXISTS group_names (name TEXT PRIMARY KEY)')
    db_op('CREATE TABLE IF NOT EXISTS members (g_name TEXT, chat_id TEXT, remark TEXT, UNIQUE(g_name, chat_id))')

# --- 3. 界面組件 ---
def main_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📁 分組管理", callback_data='list_g')],
                                 [InlineKeyboardButton("🚀 執行群發", callback_data='start_bc')]])

def back_btn(data='list_g'):
    return [InlineKeyboardButton("⬅️ 返回", callback_data=data)]

# --- 4. 核心功能函數 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    context.user_data.clear()
    msg = "🔘 **群發系統控制台**\n請選擇以下功能進行操作："
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=main_kb(), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=main_kb(), parse_mode="Markdown")
    return MAIN_STATE

# 分組列表
async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rows = db_op("SELECT name FROM group_names", is_select=True)
    kb = [[InlineKeyboardButton(f"📂 {r['name']}", callback_data=f"v_{r['name']}")] for r in rows]
    kb.append([InlineKeyboardButton("➕ 創建新分組", callback_data='create_g')])
    kb.append([InlineKeyboardButton("⬅️ 返回主選單", callback_data='to_start')])
    await query.edit_message_text("🗂️ **分組列表**：", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

# 分組詳情 (管理核心)
async def group_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    g_name = query.data.replace("v_", "") if "v_" in query.data else context.user_data.get('active_g')
    context.user_data['active_g'] = g_name
    await query.answer()
    
    mems = db_op("SELECT chat_id, remark FROM members WHERE g_name=?", (g_name,), is_select=True)
    text = f"📂 **分組名：{g_name}**\n\n成員清單："
    kb = []
    if not mems:
        text += "\n(暫無成員)"
    else:
        for m in mems:
            text += f"\n• {m['remark']} (`{m['chat_id']}`)"
            kb.append([InlineKeyboardButton(f"🗑️ 移除 {m['remark']}", callback_data=f"rm_{m['chat_id']}")])
    
    kb.append([InlineKeyboardButton("➕ 添加群組", callback_data='add_m_flow')])
    kb.append([InlineKeyboardButton("📝 修改分組名", callback_data='ren_g_flow')])
    kb.append([InlineKeyboardButton("🔥 刪除此分組", callback_data='del_g_all')])
    kb.append(back_btn())
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

# --- 添加成員：閉環引導流 ---
async def add_m_step1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"👉 **當前分組：{context.user_data['active_g']}**\n\n請發送要添加的 **群組 ID** (需帶 -)：", 
                                                 reply_markup=InlineKeyboardMarkup([back_btn(f"v_{context.user_data['active_g']}")]))
    return ADD_ID

async def add_m_step2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.message.text.strip()
    if not cid.startswith('-'):
        await update.message.reply_text("❌ 格式錯誤！ID 必須以 - 開頭。請重新輸入：")
        return ADD_ID
    context.user_data['temp_cid'] = cid
    await update.message.reply_text(f"✅ ID `{cid}` 已記錄。\n\n**現在請告訴我，這個群叫什麼名字？**\n(例如：虎總蘭德內部群)")
    return ADD_REMARK

async def add_m_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remark = update.message.text.strip()
    g_name = context.user_data['active_g']
    cid = context.user_data['temp_cid']
    db_op("INSERT OR REPLACE INTO members (g_name, chat_id, remark) VALUES (?, ?, ?)", (g_name, cid, remark))
    await update.message.reply_text(f"✨ 保存成功！\n名稱：{remark}\nID：{cid}")
    # 完成後自動跳回詳情頁
    update.callback_query = type('obj', (object,), {'data': f"v_{g_name}", 'answer': lambda: None, 'edit_message_text': update.message.reply_text})
    return await group_detail(update, context)

# --- 5. 啟動與 Web 服務 ---
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
                CallbackQueryHandler(add_m_step1, pattern='^add_m_flow$'),
                CallbackQueryHandler(lambda u,c: u.callback_query.edit_message_text("請輸入新分組名稱：") or ADD_GNAME, pattern='^create_g$'),
                CallbackQueryHandler(lambda u,c: (db_op("DELETE FROM members WHERE chat_id=? AND g_name=?", (u.callback_query.data.replace("rm_",""), c.user_data['active_g']))) or group_detail(u,c), pattern='^rm_'),
                CallbackQueryHandler(lambda u,c: (db_op("DELETE FROM group_names WHERE name=?", (c.user_data['active_g'],)), db_op("DELETE FROM members WHERE g_name=?", (c.user_data['active_g'],))) or list_groups(u,c), pattern='^del_g_all$'),
            ],
            ADD_GNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: db_op("INSERT OR IGNORE INTO group_names VALUES (?)", (u.message.text.strip(),)) or start(u,c))],
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_m_step2)],
            ADD_REMARK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_m_final)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
