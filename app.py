# app.py
from flask import Flask, request, render_template, jsonify, g
import sqlite3
import os
import logging
# import secrets # No longer needed for system-generated keys

app = Flask(__name__)

app.logger.setLevel(logging.INFO)

# 数据库配置
DATABASE = 'team_builder.db'

def get_db():
    """获取数据库连接 (每个请求一个，如果需要)"""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    """关闭数据库连接"""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# def generate_secret_key(length=32): # No longer needed
#     """生成一个安全的随机十六进制字符串作为密钥"""
#     return secrets.token_hex(length // 2)

def init_db():
    """初始化数据库"""
    db_exists = os.path.exists(DATABASE)
    app.logger.info(f"数据库 {DATABASE} {'已存在' if db_exists else '不存在，正在创建...'}")
    try:
        with app.app_context():
            conn = sqlite3.connect(DATABASE)
            cursor = conn.cursor()
            
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
            
            conn.commit()
            conn.close()
            if not db_exists:
                 app.logger.info(f"数据库 {DATABASE} 初始化完成。")
    except sqlite3.Error as e:
        app.logger.error(f"数据库初始化失败: {e}")


# --- Helper function to verify secret key ---
def verify_user_secret(db, user_id, provided_secret):
    # This helper is still useful for existing users
    if not provided_secret: # This check might be redundant if API enforces secret presence
        return False, "密钥不能为空"
    cursor = db.execute('SELECT secret_key FROM user_secrets WHERE user_id = ?', (user_id,))
    secret_row = cursor.fetchone()
    if not secret_row: # Should not happen if called for existing user context
        return False, "用户不存在或没有密钥记录"
    if secret_row['secret_key'] == provided_secret:
        return True, "密钥验证成功"
    else:
        return False, "密钥无效"

# --- 页面路由 ---

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

# --- API 路由 ---

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
    """为用户添加商品金额。用户首次添加时，必须提供密钥以创建账户。"""
    data = request.json
    user_id = data.get('user_id')
    item_value_str = data.get('item_value')
    user_defined_secret = data.get('secret_key')

    if not user_id or not item_value_str:
        return jsonify({"success": False, "message": "用户ID和商品金额不能为空"}), 400
    if not user_defined_secret: # Secret is now always required for add_item
        return jsonify({"success": False, "message": "密钥不能为空。新用户首次添加金额时需设定密钥，现有用户需提供密钥。"}), 400


    try:
        item_value = int(item_value_str)
        if item_value <= 0:
            raise ValueError("金额必须是正数")
        
        with get_db() as db:
            cursor = db.execute('SELECT secret_key FROM user_secrets WHERE user_id = ?', (user_id,))
            user_secret_row = cursor.fetchone()

            if user_secret_row is None: # New user
                # Store the user-defined secret
                db.execute('INSERT INTO user_secrets (user_id, secret_key) VALUES (?, ?)', (user_id, user_defined_secret))
                app.logger.info(f"新用户 '{user_id}' 创建成功，已设置用户自定义密钥。")
                message = f"新用户 '{user_id}' 创建成功，已使用您提供的密钥并添加金额 {item_value}。请妥善保管您的密钥。"
            else: # Existing user, secret must be validated
                stored_secret = user_secret_row['secret_key']
                if stored_secret != user_defined_secret:
                    app.logger.warning(f"用户 '{user_id}' 添加金额失败: 密钥无效")
                    return jsonify({"success": False, "message": "密钥无效"}), 403 # Forbidden
                message = f"已为用户 '{user_id}' 添加金额 {item_value} (密钥验证通过)。"

            # Add the item
            db.execute('INSERT INTO user_items (user_id, item_value) VALUES (?, ?)',
                       (user_id, item_value))
            db.commit()
        
        app.logger.info(f"已为用户 '{user_id}' 添加金额 {item_value}")
        return jsonify({"success": True, "message": message})

    except ValueError:
        return jsonify({"success": False, "message": "商品金额必须是有效的正整数"}), 400
    except sqlite3.IntegrityError as e: # Catch if user_id somehow already exists in user_secrets but logic failed
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
    if not provided_secret: # Secret is always required for delete_item
        return jsonify({"success": False, "message": "密钥不能为空"}), 401

    try:
        item_value = int(item_value_str)
        with get_db() as db:
            # Verify secret first
            cursor_secret = db.execute('SELECT secret_key FROM user_secrets WHERE user_id = ?', (user_id,))
            user_secret_row = cursor_secret.fetchone()

            if user_secret_row is None:
                return jsonify({"success": False, "message": f"用户 '{user_id}' 不存在。"}), 404
            if user_secret_row['secret_key'] != provided_secret:
                return jsonify({"success": False, "message": "密钥无效"}), 403

            # Proceed with deletion
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
    if not provided_secret: # Secret is always required for delete_user
        return jsonify({"success": False, "message": "密钥不能为空"}), 401
        
    try:
        with get_db() as db:
            # Verify secret first
            cursor_secret = db.execute('SELECT secret_key FROM user_secrets WHERE user_id = ?', (user_id,))
            user_secret_row = cursor_secret.fetchone()

            if user_secret_row is None: # If user doesn't even exist in secrets, they can't be deleted by this mechanism
                return jsonify({"success": False, "message": f"用户 '{user_id}' 不存在。"}), 404
            if user_secret_row['secret_key'] != provided_secret:
                return jsonify({"success": False, "message": "密钥无效"}), 403
            
            # Proceed with deletion
            db.execute('DELETE FROM user_items WHERE user_id = ?', (user_id,))
            db.execute('DELETE FROM user_secrets WHERE user_id = ?', (user_id,)) # Also delete from secrets table
            db.commit()

        app.logger.info(f"已删除用户 '{user_id}' 及其所有商品金额和密钥 (密钥验证通过)")
        return jsonify({"success": True, "message": f"已成功删除用户 '{user_id}' 及其所有数据和密钥"})
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

# find_teams function remains unchanged from your provided code
@app.route('/api/find_teams', methods=['POST'])
def find_teams():
    """查找能够凑成指定金额的队伍组合，使用动态规划方法"""
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
    iteration_count = 0
    combination_count = 0
    max_team_size = data.get('max_team_size', 10)
    max_combinations_per_sum = data.get('max_combinations_per_sum', 10)

    for user_id_item, item_value_item in all_users_items:
        iteration_count += 1
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
                if len(prev_team_list) >= max_team_size:
                    continue 
                if any(uid == user_id_item for uid, _ in prev_team_list):
                    continue 

                new_team_list = prev_team_list + [(user_id_item, item_value_item)]
                team_as_set_for_deduplication = frozenset(new_team_list)

                if team_as_set_for_deduplication not in current_dp_entry_set:
                    current_dp_entry_set.add(team_as_set_for_deduplication)
                    current_dp_entry_list.append((new_team_list, current_target_s))
                    combination_count += 1
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
    
    found_teams.sort(key=len)
    return jsonify({"success": True, "teams": found_teams})

if __name__ == '__main__':
    with app.app_context():
        init_db() 
    app.run(debug=True)