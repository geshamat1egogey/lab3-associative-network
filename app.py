from flask import Flask, request, jsonify, render_template
import os
import uuid
import json
import random
import pandas as pd
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Boolean, Float

app = Flask(__name__)

# --- НАСТРОЙКА БАЗЫ ДАННЫХ ---
# Если код запущен на Render, используем вашу базу данных PostgreSQL
if os.environ.get('RENDER'):
    DB_URL = "postgresql://lab3_db_6uur_user:sL2ZWtaAorXBNv9v5HEUz5dIKQZkwtz1@dpg-db16jg5g1s2s738q9bk0-a/lab3_db_6uur"
else:
    # Если запущен на домашнем ПК, используем локальный файл
    DB_URL = "sqlite:///local_logs.db"

engine = create_engine(DB_URL)
metadata = MetaData()

# Описываем структуру таблицы для логов (заменяет старый CSV)
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

# Автоматически создаем таблицу в базе, если ее еще нет
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
    questions = []
    all_properties = set()
    for data in kb.values():
        all_properties.update(data.get("properties", []))
    all_properties = list(all_properties)
    
    for concept, data in kb.items():
        ancestors = get_all_ancestors(kb, concept)
        exceptions = data.get("exceptions", {})
        
        # Вопросы на конкретные свойства
        for prop in data.get("properties", []):
            questions.append({"concept_1": concept, "concept_2": prop, "text": f"Свойственно ли понятию '{concept}' {prop}?", "is_correct": True})
            
        # Вопросы на иерархию
        for ancestor in ancestors:
            questions.append({"concept_1": concept, "concept_2": ancestor, "text": f"Является ли '{concept}' категорией '{ancestor}'?", "is_correct": True})
            
        # Вопросы на абстрактные (наследуемые) свойства
        for ancestor in ancestors:
            for prop in kb[ancestor].get("properties", []):
                if prop in exceptions:
                    is_corr = exceptions[prop]
                    questions.append({"concept_1": concept, "concept_2": prop, "text": f"Свойственно ли понятию '{concept}' {prop}?", "is_correct": is_corr})
                else:
                    questions.append({"concept_1": concept, "concept_2": prop, "text": f"Свойственно ли понятию '{concept}' {prop}?", "is_correct": True})

        # Ложные вопросы для кнопки "Нет"
        valid_false_props = [p for p in all_properties if p not in data.get("properties", []) and not any(p in kb[anc].get("properties", []) for anc in ancestors)]
        false_props = random.sample(valid_false_props, min(3, len(valid_false_props)))
        for prop in false_props:
            questions.append({"concept_1": concept, "concept_2": prop, "text": f"Свойственно ли понятию '{concept}' {prop}?", "is_correct": False})

    # Очистка от дубликатов
    unique_questions = []
    seen = set()
    for q in questions:
        pair = (q['concept_1'], q['concept_2'])
        if pair not in seen:
            seen.add(pair)
            q['id'] = f"q_{len(unique_questions) + 1}"
            unique_questions.append(q)

    random.shuffle(unique_questions)
    # База вопросов для одного человека должна составлять порядка 100 вопросов[cite: 1]
    return unique_questions[:100]


# --- МАРШРУТЫ СЕРВЕРА ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/results')
def results():
    return render_template('results.html')

@app.route('/api/start_session', methods=['GET'])
def start_session():
    kb = load_kb()
    questions = generate_questions(kb)
    return jsonify({"user_id": str(uuid.uuid4()), "questions": questions})

@app.route('/api/save_log', methods=['POST'])
def save_log():
    data = request.get_json()
    # Программа автоматически записывает результаты ответов в базу данных PostgreSQL[cite: 1]
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
    # Читаем все данные из БД прямо в pandas DataFrame
    with engine.connect() as conn:
        df = pd.read_sql(test_logs.select(), conn)
    
    if df.empty:
        return jsonify({"error": "Нет данных для анализа. Пройдите тестирование."})
    
    # Фильтруем: берем только верные ответы для построения сети
    df = df[(df['is_correct_logic'] == True) & (df['user_answer'] == True)]
    
    if df.empty:
        return jsonify({"error": "Нет правильных ответов для построения сети."})
        
    # Произвести усреднение времен ответов на вопросы по группе людей и вычислить дисперсии[cite: 1]
    stats = df.groupby(['concept_1', 'concept_2'])['reaction_time_sec'].agg(
        mean_time='mean', variance='var', answers_count='count'
    ).reset_index().fillna(0).sort_values(by='mean_time')
    
    nodes_set = set(stats['concept_1']).union(set(stats['concept_2']))
    nodes = [{"id": n, "label": n} for n in nodes_set]
    
    edges = []
    # Построение сети рекомендуется начинать с пар понятий, которым соответствуют наименьшие времена ответов[cite: 1]
    threshold_index = max(1, len(stats) // 3)
    fastest_pairs = stats.head(threshold_index)
    
    for _, row in fastest_pairs.iterrows():
        edges.append({
            "from": row['concept_1'], "to": row['concept_2'],
            "label": f"{row['mean_time']:.2f}s", "arrows": "to"
        })
        
    return jsonify({"stats": stats.to_dict('records'), "nodes": nodes, "edges": edges})

if __name__ == '__main__':
    app.run(debug=True)