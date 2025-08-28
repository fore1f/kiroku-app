import os
import json
from datetime import datetime, time
import pytz
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///kiroku.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = "このページにアクセスするにはログインが必要です。"

# --- タイムゾーン設定 ---
JST = pytz.timezone('Asia/Tokyo')

# --- 部位定義 ---
STIFFNESS_FINGER_PARTS = {
    'R': {'R_Thumb': '親指', 'R_Index': '人差し指', 'R_Middle': '中指', 'R_Ring': '薬指', 'R_Pinky': '小指'},
    'L': {'L_Thumb': '親指', 'L_Index': '人差し指', 'L_Middle': '中指', 'L_Ring': '薬指', 'L_Pinky': '小指'}
}

# --- データベースモデル定義 ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    records = db.relationship('Record', backref='author', lazy=True, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Record(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    numbness_strength = db.Column(db.Integer, default=0)
    numbness_parts = db.Column(db.String(200), default='')
    stiffness = db.Column(db.Text, default='{}')
    memo = db.Column(db.Text, default='')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# --- Flask-Login設定 ---
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- カスタムフィルタ ---
@app.template_filter('stiffness_name')
def stiffness_name_filter(part_id):
    for hand in STIFFNESS_FINGER_PARTS.values():
        if part_id in hand:
            return hand[part_id]
    return part_id

@app.template_filter('to_jst')
def to_jst_filter(utc_dt):
    if utc_dt is None:
        return ""
    return utc_dt.replace(tzinfo=pytz.utc).astimezone(JST).strftime('%Y-%m-%d %H:%M')

@app.template_filter('to_jst_time')
def to_jst_time_filter(utc_dt):
    if utc_dt is None:
        return ""
    return utc_dt.replace(tzinfo=pytz.utc).astimezone(JST).strftime('%H:%M')

# --- ヘルスチェック用ルート ---
@app.route('/health')
def health_check():
    return "OK", 200

# --- ルート定義 ---
@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        try:
            date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        except ValueError:
            flash('日付の形式が正しくありません。', 'danger')
            return redirect(url_for('index'))

        stiffness_data = {
            'parts': request.form.getlist('stiffness_parts'),
            'strength': {
                'R_Hand': request.form.get('stiffness_strength_R_Hand', '0'),
                'L_Hand': request.form.get('stiffness_strength_L_Hand', '0'),
                'R_Knee': request.form.get('stiffness_strength_R_Knee', '0'),
                'L_Knee': request.form.get('stiffness_strength_L_Knee', '0'),
            }
        }

        new_record = Record(
            date=date,
            numbness_strength=request.form.get('numbness_strength', 0, type=int),
            numbness_parts=','.join(request.form.getlist('numbness_parts')),
            stiffness=json.dumps(stiffness_data, ensure_ascii=False),
            memo=request.form['memo'],
            author=current_user
        )
        db.session.add(new_record)
        db.session.commit()
        flash('記録が保存されました。', 'success')
        return redirect(url_for('index'))

    records = Record.query.filter_by(author=current_user).order_by(Record.date.desc(), Record.created_at.desc()).all()
    for record in records:
        try:
            record.stiffness_data = json.loads(record.stiffness)
        except json.JSONDecodeError:
            record.stiffness_data = {'parts': [], 'strength': {}}

    return render_template('index.html', 
                           records=records, 
                           today=datetime.now().strftime('%Y-%m-%d'),
                           stiffness_finger_parts=STIFFNESS_FINGER_PARTS)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        password2 = request.form['password2']

        if password != password2:
            flash('パスワードが一致しません。', 'danger')
            return redirect(url_for('register'))

        user = User.query.filter_by(username=username).first()
        if user:
            flash('そのユーザー名は既に使用されています。', 'danger')
            return redirect(url_for('register'))

        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash('登録が完了しました。ログインしてください。', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user is None or not user.check_password(password):
            flash('ユーザー名またはパスワードが正しくありません。', 'danger')
            return redirect(url_for('login'))
        login_user(user, remember=True)
        flash('ログインしました。', 'success')
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('ログアウトしました。', 'info')
    return redirect(url_for('login'))

@app.route('/delete/<int:record_id>', methods=['POST'])
@login_required
def delete_record(record_id):
    record = db.session.get(Record, record_id)
    if record is None:
        abort(404) # Not Found
    if record.author != current_user:
        abort(403) # Forbidden
    db.session.delete(record)
    db.session.commit()
    flash('記録を削除しました。', 'success')
    return redirect(url_for('index'))

@app.route('/delete_account', methods=['POST'])
@login_required
def delete_account():
    db.session.delete(current_user)
    db.session.commit()
    logout_user()
    flash('アカウントとすべての記録が削除されました。', 'info')
    return redirect(url_for('login'))

@app.route('/report')
@login_required
def report():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    if not start_date_str or not end_date_str:
        flash('レポートの期間を指定してください。', 'warning')
        return redirect(url_for('index'))

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
    except ValueError:
        flash('日付の形式が正しくありません。', 'danger')
        return redirect(url_for('index'))

    start_date_utc = JST.localize(start_date).astimezone(pytz.utc)
    end_date_utc = JST.localize(end_date).astimezone(pytz.utc)

    records = Record.query.filter(
        Record.user_id == current_user.id,
        Record.created_at >= start_date_utc,
        Record.created_at <= end_date_utc
    ).order_by(Record.created_at.asc()).all()

    # グラフ用データを作成
    labels = []
    for record in records:
        jst_time_part = record.created_at.replace(tzinfo=pytz.utc).astimezone(JST).strftime('%H:%M')
        date_part = record.date.strftime('%m/%d')
        labels.append(f"{date_part} {jst_time_part}")

    numbness_data = [record.numbness_strength for record in records]
    labels.reverse()
    numbness_data.reverse()
    
    stiffness_r_hand_data = []
    stiffness_l_hand_data = []
    stiffness_r_knee_data = []
    stiffness_l_knee_data = []

    for record in records:
        try:
            stiffness_dict = json.loads(record.stiffness)
            strength = stiffness_dict.get('strength', {})
            stiffness_r_hand_data.append(int(strength.get('R_Hand', 0)))
            stiffness_l_hand_data.append(int(strength.get('L_Hand', 0)))
            stiffness_r_knee_data.append(int(strength.get('R_Knee', 0)))
            stiffness_l_knee_data.append(int(strength.get('L_Knee', 0)))
            record.stiffness_data = stiffness_dict # テンプレートのテーブル表示用
        except json.JSONDecodeError:
            stiffness_r_hand_data.append(0)
            stiffness_l_hand_data.append(0)
            stiffness_r_knee_data.append(0)
            stiffness_l_knee_data.append(0)
            record.stiffness_data = {'parts': [], 'strength': {}}

    stiffness_r_hand_data.reverse()
    stiffness_l_hand_data.reverse()
    stiffness_r_knee_data.reverse()
    stiffness_l_knee_data.reverse()

    chart_data = {
        'labels': labels,
        'datasets': [
            {'label': 'しびれの強さ', 'data': numbness_data, 'borderColor': 'rgba(255, 99, 132, 1)'},
            {'label': 'こわばり(右手)', 'data': stiffness_r_hand_data, 'borderColor': 'rgba(54, 162, 235, 1)'},
            {'label': 'こわばり(左手)', 'data': stiffness_l_hand_data, 'borderColor': 'rgba(75, 192, 192, 1)'},
            {'label': 'こわばり(右膝)', 'data': stiffness_r_knee_data, 'borderColor': 'rgba(255, 206, 86, 1)'},
            {'label': 'こわばり(左膝)', 'data': stiffness_l_knee_data, 'borderColor': 'rgba(153, 102, 255, 1)'},
        ]
    }

    return render_template('report.html', 
                           records=records, 
                           start_date=start_date_str, 
                           end_date=end_date_str,
                           stiffness_finger_parts=STIFFNESS_FINGER_PARTS,
                           chart_data=chart_data)


@app.cli.command("init-db")
def init_db_command():
    """データベースを初期化します。"""
    db.create_all()
    print("データベースを初期化しました。")

# アプリケーションコンテキスト内でデータベーステーブルを作成
# gunicorn が app オブジェクトをロードする際に実行されるようにする
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)
