# app.py
from flask import Flask, request, render_template, jsonify, g
import sqlite3
import os
import logging

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# Database_config
DATABASE = 'team_builder.db'

# --- Database Utility Functions ---
def get_db():
    """获取数据库连接 (每个请求一个，如果需要)"""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row  # Access columns by name
    return db

@app.teardown_appcontext
def close_connection(exception):
    """关闭数据库连接"""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """初始化数据库"""
    db_exists = os.path.exists(DATABASE)
    app.logger.info(f"数据库 {DATABASE} {'已存在' if db_exists else '不存在，正在创建...'}")
    try:
        with app.app_context(): # Ensures 'g' is available if get_db() uses it
            conn = get_db() # Use get_db for consistency
            cursor = conn.cursor()
            
            # Table: user_items
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_items';")
            if not cursor.fetchone():
                conn.execute('''
                    CREATE TABLE user_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        item_value INTEGER NOT NULL
                    )
                ''')
                app.logger.info("表 user_items 创建成功。")
            else:
                app.logger.info("表 user_items 已存在。")

            # Table: user_secrets
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_secrets';")
            if not cursor.fetchone():
                conn.execute('''
                    CREATE TABLE user_secrets (
                        user_id TEXT PRIMARY KEY,
                        secret_key TEXT NOT NULL
                    )
                ''')
                app.logger.info("表 user_secrets 创建成功。")
            else:
                app.logger.info("表 user_secrets 已存在。")
            
            # Table: active_teams (schema updated: no current_team_size)
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='active_teams';")
            if not cursor.fetchone():
                conn.execute('''
                    CREATE TABLE active_teams (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        initiator_user_id TEXT NOT NULL,
                        amount_needed INTEGER NOT NULL,
                        team_invite_link TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (initiator_user_id) REFERENCES user_secrets(user_id),
                        UNIQUE (initiator_user_id, session_id) 
                    )
                ''')
                app.logger.info("表 active_teams 创建成功 (schema updated: no current_team_size)。")
            else:
                app.logger.info("表 active_teams 已存在。Ensure schema is up-to-date.")
            
            conn.commit()
            if not db_exists:
                 app.logger.info(f"数据库 {DATABASE} 初始化完成。")
    except sqlite3.Error as e:
        app.logger.error(f"数据库初始化失败: {e}")


# --- Helper function to verify secret key ---
def verify_user_secret(db, user_id, provided_secret):
    if not provided_secret:
        return False, "密钥不能为空"
    cursor = db.execute('SELECT secret_key FROM user_secrets WHERE user_id = ?', (user_id,))
    secret_row = cursor.fetchone()
    if not secret_row:
        return False, "用户不存在或尚未设置密钥。请先通过“我的账户”页面添加至少一个金额以注册用户并设置密钥。"
    if secret_row['secret_key'] == provided_secret:
        return True, "密钥验证成功"
    else:
        return False, "密钥无效"

def get_user_item_values(db, user_id_to_check):
    cursor = db.execute('SELECT item_value FROM user_items WHERE user_id = ? ORDER BY item_value', (user_id_to_check,))
    return [row['item_value'] for row in cursor.fetchall()]

# --- Page Rendering Routes ---
@app.route('/')
def data_entry_page():
    current_data = {} 
    try:
        with get_db() as db:
            cursor = db.execute('SELECT user_id, item_value FROM user_items ORDER BY user_id, item_value')
            all_items = cursor.fetchall()
            for row in all_items:
                uid = row['user_id']
                val = int(row['item_value'])
                if uid not in current_data:
                    current_data[uid] = []
                current_data[uid].append(val)
    except sqlite3.Error as e:
        app.logger.error(f"获取数据用于 data_entry_page 失败: {e}")
    return render_template('data_entry.html', all_user_data_initially=current_data)


@app.route('/find_team')
def team_finder_page():
    return render_template('team_finder.html')

@app.route('/initiate_team_page')
def initiate_team_page_render():
    return render_template('initiate_team_page.html')

# --- API Routes for User Items & Secrets ---
@app.route('/api/get_user_items', methods=['GET'])
def get_user_items():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({"error": "请提供用户ID"}), 400
    items = []
    try:
        with get_db() as db:
            cursor = db.execute('SELECT item_value FROM user_items WHERE user_id = ? ORDER BY item_value', (user_id,))
            items = [row['item_value'] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        app.logger.error(f"获取用户 {user_id} 商品失败: {e}")
        return jsonify({"error": "数据库查询失败"}), 500
    return jsonify({"user_id": user_id, "items": items})

@app.route('/api/add_item', methods=['POST'])
def add_item():
    data = request.json
    user_id = data.get('user_id')
    item_value_str = data.get('item_value')
    user_defined_secret = data.get('secret_key')

    if not user_id or not item_value_str:
        return jsonify({"success": False, "message": "用户ID和商品金额不能为空"}), 400
    if not user_defined_secret:
        return jsonify({"success": False, "message": "密钥不能为空。新用户首次添加金额时需设定密钥，现有用户需提供密钥。"}), 400

    try:
        item_value = int(item_value_str)
        if item_value <= 0:
            raise ValueError("金额必须是正数")
        
        with get_db() as db:
            cursor = db.execute('SELECT secret_key FROM user_secrets WHERE user_id = ?', (user_id,))
            user_secret_row = cursor.fetchone()

            if user_secret_row is None: # New user
                db.execute('INSERT INTO user_secrets (user_id, secret_key) VALUES (?, ?)', (user_id, user_defined_secret))
                app.logger.info(f"新用户 '{user_id}' 创建成功，已设置用户自定义密钥。")
                message = f"新用户 '{user_id}' 创建成功，已使用您提供的密钥并添加金额 {item_value}。请妥善保管您的密钥。"
            else: # Existing user, secret must be validated
                stored_secret = user_secret_row['secret_key']
                if stored_secret != user_defined_secret:
                    app.logger.warning(f"用户 '{user_id}' 添加金额失败: 密钥无效")
                    return jsonify({"success": False, "message": "密钥无效"}), 403
                message = f"已为用户 '{user_id}' 添加金额 {item_value} (密钥验证通过)。"

            db.execute('INSERT INTO user_items (user_id, item_value) VALUES (?, ?)', (user_id, item_value))
            db.commit()
        
        app.logger.info(f"已为用户 '{user_id}' 添加金额 {item_value}")
        return jsonify({"success": True, "message": message})

    except ValueError:
        return jsonify({"success": False, "message": "商品金额必须是有效的正整数"}), 400
    except sqlite3.IntegrityError as e:
        app.logger.error(f"为用户 '{user_id}' 添加密钥或项目时发生完整性错误: {e}")
        return jsonify({"success": False, "message": "设置用户密钥时出错，可能用户已存在但处理逻辑异常。"}), 500
    except sqlite3.Error as e:
        app.logger.error(f"为用户 '{user_id}' 添加金额 {item_value_str} 失败: {e}")
        return jsonify({"success": False, "message": "数据库操作失败"}), 500

@app.route('/api/delete_item', methods=['POST'])
def delete_item():
    data = request.json
    user_id = data.get('user_id')
    item_value_str = data.get('item_value')
    provided_secret = data.get('secret_key')

    if not user_id or not item_value_str:
        return jsonify({"success": False, "message": "用户ID和商品金额不能为空"}), 400
    if not provided_secret:
        return jsonify({"success": False, "message": "密钥不能为空"}), 401

    try:
        item_value = int(item_value_str)
        with get_db() as db:
            is_valid, message = verify_user_secret(db, user_id, provided_secret)
            if not is_valid: # verify_user_secret handles "user not found" case
                return jsonify({"success": False, "message": message}), 403

            cursor = db.execute('SELECT id FROM user_items WHERE user_id = ? AND item_value = ? LIMIT 1',
                                (user_id, item_value))
            row = cursor.fetchone()
            
            if row:
                item_id_to_delete = row['id']
                db.execute('DELETE FROM user_items WHERE id = ?', (item_id_to_delete,))
                db.commit()
                app.logger.info(f"已从用户 '{user_id}' 删除金额 {item_value} (ID: {item_id_to_delete}) (密钥验证通过)")
                return jsonify({"success": True, "message": f"已从用户 '{user_id}' 删除金额 {item_value}"})
            else:
                return jsonify({"success": False, "message": f"用户 '{user_id}' 的列表中未找到金额 {item_value}"}), 404
    except ValueError:
        return jsonify({"success": False, "message": "商品金额必须是有效的整数"}), 400
    except sqlite3.Error as e:
        app.logger.error(f"从用户 '{user_id}' 删除金额 {item_value_str} 失败: {e}")
        return jsonify({"success": False, "message": "数据库操作失败"}), 500

@app.route('/api/delete_user', methods=['POST'])
def delete_user():
    data = request.json
    user_id = data.get('user_id')
    provided_secret = data.get('secret_key')

    if not user_id:
        return jsonify({"success": False, "message": "用户ID不能为空"}), 400
    if not provided_secret:
        return jsonify({"success": False, "message": "密钥不能为空"}), 401
        
    try:
        with get_db() as db:
            is_valid, message = verify_user_secret(db, user_id, provided_secret)
            if not is_valid: # verify_user_secret handles "user not found" case
                return jsonify({"success": False, "message": message}), 403
            
            db.execute('DELETE FROM user_items WHERE user_id = ?', (user_id,))
            db.execute('DELETE FROM user_secrets WHERE user_id = ?', (user_id,))
            # Also delete any active teams initiated by this user
            db.execute('DELETE FROM active_teams WHERE initiator_user_id = ?', (user_id,))
            db.commit()

        app.logger.info(f"已删除用户 '{user_id}' 及其所有商品金额、密钥以及发起的队伍 (密钥验证通过)")
        return jsonify({"success": True, "message": f"已成功删除用户 '{user_id}' 及其所有数据、密钥和发起的队伍"})
    except sqlite3.Error as e:
        app.logger.error(f"删除用户 '{user_id}' 失败: {e}")
        return jsonify({"success": False, "message": "数据库操作失败"}), 500

@app.route('/api/get_all_data', methods=['GET'])
def get_all_data():
    all_data = {}
    try:
        with get_db() as db:
            cursor = db.execute('SELECT user_id, item_value FROM user_items ORDER BY user_id, item_value')
            all_items = cursor.fetchall()
            for row in all_items:
                uid = row['user_id']
                val = int(row['item_value'])
                if uid not in all_data:
                    all_data[uid] = []
                all_data[uid].append(val)
        return jsonify({"success": True, "all_user_data": all_data})
    except sqlite3.Error as e:
        app.logger.error(f"获取所有数据失败: {e}")
        return jsonify({"success": False, "message": "数据库查询失败"}), 500

# --- API Route for Finding Teams  ---
@app.route('/api/find_teams', methods=['POST'])
def find_teams():
    data = request.json
    initiator_id = data.get('user_id')
    target_sum_str = data.get('target_sum')
    
    app.logger.info(f"\n=== 开始查找队伍 ===")
    app.logger.info(f"发起用户ID: {initiator_id}")
    app.logger.info(f"目标金额: {target_sum_str}")

    if not initiator_id or not target_sum_str:
        return jsonify({"success": False, "message": "发起用户ID和目标金额不能为空"}), 400

    try:
        target_sum = int(target_sum_str)
        if target_sum <= 0:
            raise ValueError("目标金额必须是正数")
    except ValueError:
        return jsonify({"success": False, "message": "目标金额必须是有效的正整数"}), 400

    user_data = {}
    try:
        with get_db() as db:
            cursor = db.execute('SELECT user_id, item_value FROM user_items ORDER BY user_id')
            raw_items = cursor.fetchall()
            for row in raw_items:
                uid = row['user_id']
                val = int(row['item_value'])
                if uid not in user_data:
                    user_data[uid] = []
                user_data[uid].append(val)
    except sqlite3.Error as e:
        app.logger.error(f"查找队伍时获取用户数据失败: {e}")
        return jsonify({"success": False, "message": "数据库查询失败"}), 500
    
    if initiator_id not in user_data or not user_data[initiator_id]:
        return jsonify({"success": False, "message": f"发起用户 '{initiator_id}' 不存在或没有商品"}), 404

    all_users_items = []
    total_sum_available = 0
    min_item_value = float('inf')

    initiator_items_sorted = sorted(user_data.get(initiator_id, []), reverse=True)
    for item_val in initiator_items_sorted:
        all_users_items.append((initiator_id, item_val))
        total_sum_available += item_val
        min_item_value = min(min_item_value, item_val)
    
    for user_id_iter, items_iter in user_data.items():
        if user_id_iter != initiator_id:
            sorted_items_iter = sorted(items_iter, reverse=True)
            for item_val in sorted_items_iter:
                all_users_items.append((user_id_iter, item_val))
                total_sum_available += item_val
                min_item_value = min(min_item_value, item_val)
    
    if total_sum_available < target_sum:
        app.logger.info("总可用金额小于目标金额，无法找到有效组合")
        return jsonify({"success": True, "teams": []})

    if min_item_value == float('inf') and target_sum > 0 :
        app.logger.info("没有任何物品可供选择。")
        return jsonify({"success": True, "teams": []})

    if target_sum > 0 and target_sum < min_item_value :
        app.logger.info("目标金额小于最小物品金额，无法找到有效组合")
        return jsonify({"success": True, "teams": []})

    dp = {0: (set(), [([], 0)])} 
    
    app.logger.info(f"\n=== 开始动态规划 ===")
    app.logger.info(f"目标金额: {target_sum}")

    found_teams = []
    max_team_size = data.get('max_team_size', 10) # Default max team size
    max_combinations_per_sum = data.get('max_combinations_per_sum', 10) # Optimization

    for user_id_item, item_value_item in all_users_items:
        for current_target_s in range(target_sum, item_value_item - 1, -1):
            prev_target_s = current_target_s - item_value_item
            if prev_target_s not in dp:
                continue

            prev_combinations_set, prev_combinations_list_orig = dp[prev_target_s]
            
            prev_combinations_list = prev_combinations_list_orig
            if len(prev_combinations_list_orig) > max_combinations_per_sum:
                prev_combinations_list = prev_combinations_list_orig[:max_combinations_per_sum]


            if not prev_combinations_list:
                continue

            current_dp_entry_set, current_dp_entry_list = dp.get(current_target_s, (set(), []))
            
            if len(current_dp_entry_list) >= max_combinations_per_sum:
                continue

            new_combinations_added_for_current_target_s = False
            for prev_team_list, _ in prev_combinations_list:
                if len(prev_team_list) >= max_team_size: # Max team size constraint
                    continue 
                if any(uid == user_id_item for uid, _ in prev_team_list): # User can't be in team twice
                    continue 

                new_team_list = prev_team_list + [(user_id_item, item_value_item)]
                team_as_set_for_deduplication = frozenset(new_team_list) # For deduplication

                if team_as_set_for_deduplication not in current_dp_entry_set:
                    current_dp_entry_set.add(team_as_set_for_deduplication)
                    current_dp_entry_list.append((new_team_list, current_target_s))
                    new_combinations_added_for_current_target_s = True
                    
                    if current_target_s == target_sum:
                        if any(uid == initiator_id for uid, _ in new_team_list):
                            team_details = [{"user_id": uid, "item_value": val} for uid, val in new_team_list]

                            if team_details not in found_teams: 
                                found_teams.append(team_details)
                    
                    if len(current_dp_entry_list) >= max_combinations_per_sum:
                        break 
            
            if new_combinations_added_for_current_target_s:
                 dp[current_target_s] = (current_dp_entry_set, current_dp_entry_list)
    
    found_teams.sort(key=len) # Sort by team size
    return jsonify({"success": True, "teams": found_teams})

# --- API Routes for Active Team Postings ---
@app.route('/api/initiate_team_posting', methods=['POST'])
def initiate_team_posting():
    data = request.json
    session_id = data.get('sessionId')
    user_id = data.get('userId')
    secret_key = data.get('secretKey')
    amount_needed = data.get('amountNeeded')
    team_invite_link = data.get('teamInviteLink')

    if not all([session_id, user_id, secret_key, amount_needed is not None, team_invite_link]):
        return jsonify({"success": False, "message": "所有字段均为必填项 (场次、用户ID、密钥、所需金额、邀请链接)。"}), 400
    
    try:
        amount_needed = int(amount_needed)
        if amount_needed < 0:
            raise ValueError("所需金额不能为负。")
    except ValueError as e:
        return jsonify({"success": False, "message": f"输入无效: {e}"}), 400

    with get_db() as db:
        is_valid, message = verify_user_secret(db, user_id, secret_key)
        if not is_valid:
            return jsonify({"success": False, "message": message}), 403

        try:
            db.execute('''
                INSERT INTO active_teams (session_id, initiator_user_id, amount_needed, team_invite_link, created_at, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ''', (session_id, user_id, amount_needed, team_invite_link))
            db.commit()
            app.logger.info(f"用户 '{user_id}' 为场次 '{session_id}' 发起组队成功。")
            return jsonify({"success": True, "message": "组队发起成功！"})
        except sqlite3.IntegrityError:
            app.logger.warning(f"用户 '{user_id}' 尝试为场次 '{session_id}' 重复发起组队。")
            return jsonify({"success": False, "message": "您已经为该场次发起过组队。如需修改，请使用更新功能。"}), 409
        except sqlite3.Error as e:
            app.logger.error(f"发起组队数据库操作失败: {e}")
            return jsonify({"success": False, "message": "数据库操作失败，无法发起组队。"}), 500

@app.route('/api/update_team_posting', methods=['POST'])
def update_team_posting():
    data = request.json
    session_id = data.get('sessionId')
    user_id = data.get('userId')
    secret_key = data.get('secretKey')
    amount_needed = data.get('amountNeeded')
    team_invite_link = data.get('teamInviteLink')

    if not all([session_id, user_id, secret_key, amount_needed is not None, team_invite_link]):
        return jsonify({"success": False, "message": "所有字段均为必填项。"}), 400

    try:
        amount_needed = int(amount_needed)
        if amount_needed < 0:
            raise ValueError("所需金额不能为负。")
    except ValueError as e:
        return jsonify({"success": False, "message": f"输入无效: {e}"}), 400

    with get_db() as db:
        is_valid, message = verify_user_secret(db, user_id, secret_key)
        if not is_valid:
            return jsonify({"success": False, "message": message}), 403
        
        try:
            cursor = db.execute('''
                UPDATE active_teams 
                SET amount_needed = ?, team_invite_link = ?, updated_at = CURRENT_TIMESTAMP
                WHERE initiator_user_id = ? AND session_id = ?
            ''', (amount_needed, team_invite_link, user_id, session_id))
            db.commit()
            if cursor.rowcount == 0:
                app.logger.warning(f"用户 '{user_id}' 尝试更新不存在的场次 '{session_id}' 队伍信息，或非本人队伍。")
                return jsonify({"success": False, "message": "未找到您发起的该场次队伍信息，或更新失败。"}), 404
            app.logger.info(f"用户 '{user_id}' 为场次 '{session_id}' 更新组队信息成功。")
            return jsonify({"success": True, "message": "队伍信息更新成功！"})
        except sqlite3.Error as e:
            app.logger.error(f"更新组队信息数据库操作失败: {e}")
            return jsonify({"success": False, "message": "数据库操作失败，无法更新队伍信息。"}), 500

@app.route('/api/delete_team_posting', methods=['POST'])
def delete_team_posting():
    data = request.json
    session_id = data.get('sessionId')
    user_id = data.get('userId')
    secret_key = data.get('secretKey')

    if not all([session_id, user_id, secret_key]):
        return jsonify({"success": False, "message": "场次编号、用户ID和密钥均为必填项。"}), 400

    with get_db() as db:
        is_valid, message = verify_user_secret(db, user_id, secret_key)
        if not is_valid:
            return jsonify({"success": False, "message": message}), 403

        try:
            cursor = db.execute('''
                DELETE FROM active_teams 
                WHERE initiator_user_id = ? AND session_id = ?
            ''', (user_id, session_id))
            db.commit()
            if cursor.rowcount == 0:
                app.logger.warning(f"用户 '{user_id}' 尝试删除不存在的场次 '{session_id}' 队伍信息，或非本人队伍。")
                return jsonify({"success": False, "message": "未找到您发起的该场次队伍信息，或删除失败。"}), 404
            app.logger.info(f"用户 '{user_id}' 为场次 '{session_id}' 删除组队信息成功。")
            return jsonify({"success": True, "message": "队伍信息删除成功！"})
        except sqlite3.Error as e:
            app.logger.error(f"删除组队信息数据库操作失败: {e}")
            return jsonify({"success": False, "message": "数据库操作失败，无法删除队伍信息。"}), 500


@app.route('/api/get_user_team_postings', methods=['GET'])
def get_user_team_postings():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({"success": False, "message": "请提供用户ID"}), 400
    
    teams_data = []
    try:
        with get_db() as db:
            cursor = db.execute('''
                SELECT id, session_id, initiator_user_id, amount_needed, team_invite_link, created_at, updated_at 
                FROM active_teams 
                WHERE initiator_user_id = ? 
                ORDER BY updated_at DESC
            ''', (user_id,))
            raw_teams = cursor.fetchall()
            teams_data = [dict(row) for row in raw_teams] # FIX: Convert raw_teams to list of dicts
        return jsonify({"success": True, "teams": teams_data})
    except sqlite3.Error as e:
        app.logger.error(f"获取用户 '{user_id}' 的队伍列表失败: {e}")
        return jsonify({"success": False, "message": "数据库查询失败"}), 500
    except Exception as e:
        app.logger.error(f"获取用户队伍时发生意外错误: {e}")
        return jsonify({"success": False, "message": f"处理请求时发生意外错误: {str(e)}"}), 500


@app.route('/api/get_all_active_teams', methods=['GET'])
def get_all_active_teams():
    teams_data = []
    session_id_filter = request.args.get('session_id') # Get session_id from request
    
    query_sql = '''
        SELECT id, session_id, initiator_user_id, amount_needed, team_invite_link, created_at, updated_at 
        FROM active_teams 
        WHERE amount_needed > 0 
    '''
    params = []

    # Add session_id filter if provided
    if session_id_filter and session_id_filter.strip():
        query_sql += " AND session_id = ? "
        params.append(session_id_filter.strip())
    
    query_sql += " ORDER BY updated_at DESC "

    try:
        with get_db() as db:
            cursor = db.execute(query_sql, tuple(params)) # Pass params as a tuple
            raw_teams = cursor.fetchall()
            teams_data = [dict(row) for row in raw_teams] # FIX: Convert raw_teams to list of dicts
        return jsonify({"success": True, "teams": teams_data})
    except sqlite3.Error as e:
        app.logger.error(f"获取所有活动队伍列表失败: {e}")
        return jsonify({"success": False, "message": "数据库查询失败"}), 500
    except Exception as e:
        app.logger.error(f"获取所有活动队伍时发生意外错误: {e}")
        return jsonify({"success": False, "message": f"处理请求时发生意外错误: {str(e)}"}), 500

@app.route('/api/find_perfect_match_teams', methods=['GET'])
def find_perfect_match_teams_api():
    user_id_for_match = request.args.get('user_id')
    session_id_filter = request.args.get('session_id') # Optional

    if not user_id_for_match:
        return jsonify({"success": False, "message": "请提供用于匹配的用户ID。"}), 400

    matched_teams_data = []
    try:
        with get_db() as db:
            # 1. Get item values for the user_id_for_match
            user_amounts = get_user_item_values(db, user_id_for_match)
            if not user_amounts:
                app.logger.info(f"用户 '{user_id_for_match}' 没有登记金额，无法查找完美符合的队伍。")
                return jsonify({
                    "success": True, 
                    "teams": [],
                    "message": f"用户 '{user_id_for_match}' 没有登记金额。"
                })

            # 2. Get active teams, potentially filtered by session_id
            query_sql = '''
                SELECT id, session_id, initiator_user_id, amount_needed, team_invite_link, created_at, updated_at 
                FROM active_teams 
                WHERE amount_needed > 0 
            '''
            params = []

            if session_id_filter and session_id_filter.strip():
                query_sql += " AND session_id = ? "
                params.append(session_id_filter.strip())
            
            query_sql += " ORDER BY updated_at DESC "
            
            cursor = db.execute(query_sql, tuple(params))
            candidate_teams_raw = cursor.fetchall()

            # 3. Filter candidate teams against user's amounts
            for row in candidate_teams_raw:
                team_dict = dict(row)
                try:
                    amount_needed_val = int(team_dict['amount_needed'])
                    # FIX: Add logic to append to matched_teams_data if a match is found
                    if amount_needed_val in user_amounts:
                        matched_teams_data.append(team_dict)
                except ValueError:
                    app.logger.warning(f"队伍 ID {team_dict.get('id')} 的 amount_needed '{team_dict.get('amount_needed')}' 不是有效的整数。")
                    continue # Skip this team if amount_needed is not a valid integer

        app.logger.info(f"为用户 '{user_id_for_match}' (金额: {user_amounts}) 在场次 '{session_id_filter or '所有'}' 中找到 {len(matched_teams_data)} 个完美符合的队伍。")
        return jsonify({
            "success": True,
            "teams": matched_teams_data,
            "user_amounts_checked": user_amounts 
        })

    except sqlite3.Error as e:
        app.logger.error(f"查找完美符合队伍时数据库查询失败: {e}")
        return jsonify({"success": False, "message": "数据库查询失败"}), 500
    except Exception as e:
        app.logger.error(f"查找完美符合队伍时发生意外错误: {e}")
        return jsonify({"success": False, "message": f"处理请求时发生意外错误: {str(e)}"}), 500

# --- Main Execution ---
if __name__ == '__main__':
    with app.app_context(): 
        init_db() 
    app.run(debug=True, host='0.0.0.0')