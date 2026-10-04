from flask import Flask, request, jsonify, render_template
import csv
import os
import uuid
import json
import random
import glob
import pandas as pd
import math

app = Flask(__name__)
LOG_DIR = 'logs'
os.makedirs(LOG_DIR, exist_ok=True)

def load_kb():
    with open('data/food_base.json', 'r', encoding='utf-8') as f:
        return json.load(f)

# Функция для получения всех родительских категорий узла
def get_all_ancestors(kb, concept):
    ancestors = []
    current_parent = kb.get(concept, {}).get("is_a")
    while current_parent:
        ancestors.append(current_parent)
        current_parent = kb.get(current_parent, {}).get("is_a")
    return ancestors

def generate_questions(kb):
    questions = []
    
    # Собираем абсолютно все свойства из базы для генерации ложных вопросов
    all_properties = set()
    for data in kb.values():
        all_properties.update(data.get("properties", []))
    all_properties = list(all_properties)
    
    q_id = 1
    
    for concept, data in kb.items():
        ancestors = get_all_ancestors(kb, concept)
        exceptions = data.get("exceptions", {})
        
        # 1. Вопросы на конкретные свойства (Нижний уровень)
        for prop in data.get("properties", []):
            questions.append({
                "concept_1": concept, "concept_2": prop,
                "text": f"Свойственно ли понятию '{concept}' {prop}?",
                "is_correct": True
            })
            
        # 2. Вопросы на иерархию (Является ли...)
        for ancestor in ancestors:
            questions.append({
                "concept_1": concept, "concept_2": ancestor,
                "text": f"Является ли '{concept}' категорией '{ancestor}'?",
                "is_correct": True
            })
            
        # 3. Вопросы на абстрактные (наследуемые) свойства
        for ancestor in ancestors:
            for prop in kb[ancestor].get("properties", []):
                # Проверяем, нет ли этого свойства в исключениях!
                if prop in exceptions:
                    is_corr = exceptions[prop] # берем значение из JSON (обычно false)
                    questions.append({
                        "concept_1": concept, "concept_2": prop,
                        "text": f"Свойственно ли понятию '{concept}' {prop}?",
                        "is_correct": is_corr
                    })
                else:
                    questions.append({
                        "concept_1": concept, "concept_2": prop,
                        "text": f"Свойственно ли понятию '{concept}' {prop}?",
                        "is_correct": True
                    })

        # 4. Генерируем ложные вопросы (чтобы кнопка "Нет" тоже нажималась)
        # Выбираем 3 случайных свойства, которых точно нет ни у самого понятия, ни у его предков
        valid_false_props = [
            p for p in all_properties 
            if p not in data.get("properties", []) 
            and not any(p in kb[anc].get("properties", []) for anc in ancestors)
        ]
        # Берем до 3 случайных ложных свойств
        false_props = random.sample(valid_false_props, min(3, len(valid_false_props)))
        for prop in false_props:
            questions.append({
                "concept_1": concept, "concept_2": prop,
                "text": f"Свойственно ли понятию '{concept}' {prop}?",
                "is_correct": False
            })

    # Удаляем случайные дубликаты пар (Concept - Property)
    unique_questions = []
    seen = set()
    for q in questions:
        pair = (q['concept_1'], q['concept_2'])
        if pair not in seen:
            seen.add(pair)
            q['id'] = f"q_{len(unique_questions) + 1}"
            unique_questions.append(q)

    # Перемешиваем вопросы
    random.shuffle(unique_questions)
    
    # Возвращаем ровно 100 вопросов, как требует методичка
    return unique_questions[:100]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/start_session', methods=['GET'])
def start_session():
    user_id = str(uuid.uuid4())
    kb = load_kb()
    questions = generate_questions(kb)
    return jsonify({"user_id": user_id, "questions": questions})

@app.route('/api/save_log', methods=['POST'])
def save_log():
    data = request.get_json()
    user_id = data.get('user_id')
    filename = os.path.join(LOG_DIR, f'log_{user_id}.csv')
    file_exists = os.path.isfile(filename)
    
    with open(filename, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['question_id', 'concept_1', 'concept_2', 'is_correct_logic', 'user_answer', 'reaction_time_sec'])
        writer.writerow([
            data.get('question_id'),
            data.get('concept_1'),
            data.get('concept_2'),
            data.get('is_correct_logic'),
            data.get('answer'),
            data.get('reaction_time')
        ])
    return jsonify({"status": "success"})

@app.route('/results')
def results():
    # Отдаем страницу с результатами
    return render_template('results.html')

@app.route('/api/analyze', methods=['GET'])
def analyze():
    # Читаем все CSV-файлы из папки логов
    files = glob.glob(os.path.join(LOG_DIR, '*.csv'))
    if not files:
        return jsonify({"error": "Нет данных для анализа. Пройдите тестирование."})
    
    # Объединяем логи всех испытуемых в один DataFrame
    df = pd.concat([pd.read_csv(f) for f in files])
    
    # Для построения сети берем только те случаи, где логика верна и пользователь ответил "Да"
    df = df[(df['is_correct_logic'] == True) & (df['user_answer'] == True)]
    
    # Производим усреднение времен ответов и вычисляем дисперсию
    stats = df.groupby(['concept_1', 'concept_2'])['reaction_time_sec'].agg(
        mean_time='mean', 
        variance='var', 
        answers_count='count'
    ).reset_index()
    
    # Заменяем пустую дисперсию (если был только 1 ответ) на 0
    stats = stats.fillna(0)
    
    # Сортируем пары по времени ответа (от быстрых к медленным)
    stats = stats.sort_values(by='mean_time')
    
    # Подготавливаем данные для графа (узлы и ребра)
    nodes_set = set(stats['concept_1']).union(set(stats['concept_2']))
    nodes = [{"id": n, "label": n} for n in nodes_set]
    
    edges = []
    # Построение сети начинаем с пар понятий, которым соответствуют наименьшие времена ответов
    # Берем верхнюю треть самых быстрых ответов (непосредственные связи)
    threshold_index = max(1, len(stats) // 3)
    fastest_pairs = stats.head(threshold_index)
    
    for _, row in fastest_pairs.iterrows():
        edges.append({
            "from": row['concept_1'],
            "to": row['concept_2'],
            "label": f"{row['mean_time']:.2f}s",
            "arrows": "to"
        })
        
    return jsonify({
        "stats": stats.to_dict('records'),
        "nodes": nodes,
        "edges": edges
    })

if __name__ == '__main__':
    app.run(debug=True)