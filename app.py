from flask import Flask, request, jsonify, render_template, send_file
import os
import io
import uuid
import json
import random
import pandas as pd
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Boolean, Float

app = Flask(__name__)

# --- НАСТРОЙКА БАЗЫ ДАННЫХ ---
if os.path.exists('/data'):
    # Amvera Cloud: постоянное хранилище /data (не стирается при перезагрузках)
    DB_URL = "sqlite:////data/experiment_logs.db"
elif os.environ.get('RENDER'):
    # Render: облачная PostgreSQL
    DB_URL = "postgresql+psycopg2://lab3_db_6uur_user:sL2ZWtaAorXBNv9v5HEUz5dIKQZkwtz1@dpg-db16jg5g1s2s738q9bk0-a/lab3_db_6uur"
else:
    # Локальный компьютер
    DB_URL = "sqlite:///local_logs.db"

engine = create_engine(DB_URL)
metadata = MetaData()

test_logs = Table(
    'test_logs', metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', String(50)),
    Column('question_id', String(50)),
    Column('concept_1', String(100)),
    Column('concept_2', String(100)),
    Column('is_correct_logic', Boolean),
    Column('user_answer', Boolean),
    Column('reaction_time_sec', Float)
)

metadata.create_all(engine)


# --- ЛОГИКА ГЕНЕРАЦИИ ВОПРОСОВ ---
def load_kb():
    with open('data/food_base.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def get_all_ancestors(kb, concept):
    ancestors = []
    current_parent = kb.get(concept, {}).get("is_a")
    while current_parent:
        ancestors.append(current_parent)
        current_parent = kb.get(current_parent, {}).get("is_a")
    return ancestors

def generate_questions(kb):
    # Создаем генератор со строго фиксированным сидом 42 для ВСЕХ случайных операций
    rng = random.Random(42)
    questions = []
    
    all_properties_set = set()
    for data in kb.values():
        all_properties_set.update(data.get("properties", []))
    # Обязательно сортируем список, так как set() в Python меняет порядок при перезапуске!
    all_properties = sorted(list(all_properties_set))
    
    for concept, data in kb.items():
        ancestors = get_all_ancestors(kb, concept)
        exceptions = data.get("exceptions", {})
        
        # 1. Вопросы на конкретные свойства
        for prop in data.get("properties", []):
            questions.append({
                "concept_1": concept,
                "concept_2": prop,
                "text": f"{concept} — {prop}?",
                "is_correct": True
            })
            
        # 2. Вопросы на иерархию (категории)
        for ancestor in ancestors:
            questions.append({
                "concept_1": concept,
                "concept_2": ancestor,
                "text": f"{concept} — это {ancestor.lower()}?",
                "is_correct": True
            })
            
        # 3. Вопросы на абстрактные (наследуемые) свойства и исключения
        for ancestor in ancestors:
            for prop in kb[ancestor].get("properties", []):
                is_corr = exceptions.get(prop, True)
                questions.append({
                    "concept_1": concept,
                    "concept_2": prop,
                    "text": f"{concept} — {prop}?",
                    "is_correct": is_corr
                })

        # 4. Ложные вопросы для кнопки "Нет"
        valid_false_props = [
            p for p in all_properties 
            if p not in data.get("properties", []) 
            and not any(p in kb[anc].get("properties", []) for anc in ancestors)
        ]
        false_props = rng.sample(valid_false_props, min(3, len(valid_false_props)))
        for prop in false_props:
            questions.append({
                "concept_1": concept,
                "concept_2": prop,
                "text": f"{concept} — {prop}?",
                "is_correct": False
            })

    # Очистка от дубликатов
    unique_questions = []
    seen = set()
    for q in questions:
        pair = (q['concept_1'], q['concept_2'])
        if pair not in seen:
            seen.add(pair)
            q['id'] = f"q_{len(unique_questions) + 1}"
            unique_questions.append(q)

    # Перемешиваем тем же фиксированным генератором
    rng.shuffle(unique_questions)
    return unique_questions[:100]

# Генерируем 100 вопросов один раз при старте сервера, чтобы не нагружать процессор
FIXED_QUESTIONS = generate_questions(load_kb())

PRACTICE_QUESTIONS = [
    {"id": "practice_1", "concept_1": "Тренировка", "concept_2": "Да", "text": "ТРЕНИРОВКА: Небо — голубое? (Нажмите ВЛЕВО / Да)", "is_correct": True},
    {"id": "practice_2", "concept_1": "Тренировка", "concept_2": "Нет", "text": "ТРЕНИРОВКА: Камень — мягкий? (Нажмите ВПРАВО / Нет)", "is_correct": False},
    {"id": "practice_3", "concept_1": "Тренировка", "concept_2": "Да", "text": "ТРЕНИРОВКА: Лёд — холодный? (Нажмите ВЛЕВО / Да)", "is_correct": True},
    {"id": "practice_4", "concept_1": "Тренировка", "concept_2": "Нет", "text": "ТРЕНИРОВКА: Огонь — мокрый? (Нажмите ВПРАВО / Нет)", "is_correct": False},
    {"id": "practice_5", "concept_1": "Тренировка", "concept_2": "Да", "text": "ТРЕНИРОВКА: Сейчас начнется настоящий тест, готовы? (Нажмите ВЛЕВО / Да)", "is_correct": True}
]


# --- МАРШРУТЫ СЕРВЕРА ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/results')
def results():
    return render_template('results.html')

@app.route('/api/start_session', methods=['GET'])
def start_session():
    all_questions = PRACTICE_QUESTIONS + FIXED_QUESTIONS
    return jsonify({"user_id": str(uuid.uuid4()), "questions": all_questions})

@app.route('/api/save_log', methods=['POST'])
def save_log():
    data = request.get_json()
    
    if str(data.get('question_id')).startswith('practice_'):
        return jsonify({"status": "practice_ignored"})
        
    with engine.begin() as conn:
        conn.execute(test_logs.insert().values(
            user_id=data.get('user_id'),
            question_id=data.get('question_id'),
            concept_1=data.get('concept_1'),
            concept_2=data.get('concept_2'),
            is_correct_logic=data.get('is_correct_logic'),
            user_answer=data.get('answer'),
            reaction_time_sec=data.get('reaction_time')
        ))
    return jsonify({"status": "success"})

@app.route('/api/analyze', methods=['GET'])
def analyze():
    with engine.connect() as conn:
        df = pd.read_sql(test_logs.select(), conn)
    
    if df.empty:
        return jsonify({"error": "Нет данных для анализа. Пройдите тестирование."})
    
    # 1. Оставляем ТОЛЬКО ПРАВИЛЬНЫЕ ответы (Да=Да или Нет=Нет)
    correct_df = df[df['is_correct_logic'] == df['user_answer']]
    
    if correct_df.empty:
        return jsonify({"error": "Нет правильных ответов для анализа."})
        
    stats = correct_df.groupby(['concept_1', 'concept_2', 'is_correct_logic'])['reaction_time_sec'].agg(
        mean_time='mean', variance='var', answers_count='count'
    ).reset_index().fillna(0).sort_values(by='mean_time')
    
    # 2. Для построения ГРАФА берем только существующие положительные связи (где правильный ответ "Да")
    positive_stats = stats[stats['is_correct_logic'] == True]
    
    nodes_set = set(positive_stats['concept_1']).union(set(positive_stats['concept_2']))
    nodes = [{"id": n, "label": n} for n in nodes_set]
    
    edges = []
    threshold_index = max(1, len(positive_stats) // 3)
    fastest_pairs = positive_stats.head(threshold_index)
    
    for _, row in fastest_pairs.iterrows():
        edges.append({
            "from": row['concept_1'], "to": row['concept_2'],
            "label": f"{row['mean_time']:.2f}s", "arrows": "to"
        })
        
    return jsonify({"stats": stats.to_dict('records'), "nodes": nodes, "edges": edges})

@app.route('/api/download_logs')
def download_logs():
    with engine.connect() as conn:
        df = pd.read_sql(test_logs.select(), conn)
    
    if df.empty:
        return "База данных пока пуста."
        
    # Отдаем файл напрямую из оперативной памяти без создания лишних файлов на диске
    buffer = io.BytesIO()
    df.to_csv(buffer, index=False, encoding='utf-8-sig')
    buffer.seek(0)
    return send_file(buffer, mimetype='text/csv', download_name='database_dump.csv', as_attachment=True)

@app.route('/api/clear_db')
def clear_db():
    with engine.begin() as conn:
        conn.execute(test_logs.delete())
    return "База данных успешно очищена! <br><br> <a href='/'>Вернуться на главную страницу</a>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)