#!/usr/bin/env python3
import re
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from typing import Dict, Optional, List, Any

# Конфигурация единиц измерения
# Вынесена в константу для удобства редактирования
UNITS_CONFIG = {
    'length': {
        'label': 'Длина',
        'units': {
            'km': {'name': 'Километры', 'factor': 1000.0},
            'm': {'name': 'Метры', 'factor': 1.0},
            'cm': {'name': 'Сантиметры', 'factor': 0.01},
            'mm': {'name': 'Миллиметры', 'factor': 0.001},
            'miles': {'name': 'Мили', 'factor': 1609.34},
            'yards': {'name': 'Ярды', 'factor': 0.9144},
            'feet': {'name': 'Футы', 'factor': 0.3048},
            'inches': {'name': 'Дюймы', 'factor': 0.0254}
        }
    },
    'weight': {
        'label': 'Вес',
        'units': {
            'kg': {'name': 'Килограммы', 'factor': 1.0},
            'g': {'name': 'Граммы', 'factor': 0.001},
            'mg': {'name': 'Миллиграммы', 'factor': 0.000001},
            'pounds': {'name': 'Фунты', 'factor': 0.453592},
            'ounces': {'name': 'Унции', 'factor': 0.0283495}
        }
    },
    'temperature': {
        'label': 'Температура',
        'units': {
            'c': {'name': 'Цельсий'},
            'f': {'name': 'Фаренгейт'},
            'k': {'name': 'Кельвин'}
        }
    },
    'volume': {
        'label': 'Объем',
        'units': {
            'liters': {'name': 'Литры', 'factor': 1.0},
            'ml': {'name': 'Миллилитры', 'factor': 0.001},
            'gallons': {'name': 'Галлоны', 'factor': 3.78541}
        }
    }
}


class TemplateEngine:
    """
    Простой шаблонизатор для рендеринга HTML без внешних зависимостей (типа Jinja2).
    Поддерживает {{ var }}, {% if var %} и {% for item in list %}.
    """

    def __init__(self, template_name: str):
        self.template_path = Path(__file__).parent / template_name

    def render(self, context: Dict[str, Any]) -> str:
        if not self.template_path.exists():
            return "<h1>Error: Template file not found.</h1>"

        with open(self.template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 1. Обработка циклов (самая сложная часть, делаем сначала)
        # Ищем паттерн: {% for item in history %} ... {% else %} ... {% endfor %}
        loop_pattern = re.compile(r'\{% for item in (\w+) %\}(.*?)\{% else %\}(.*?)\{% endfor %\}', re.DOTALL)

        def loop_replacer(match):
            list_name = match.group(1)
            loop_body = match.group(2)
            else_body = match.group(3)

            items = context.get(list_name, [])
            if not items:
                return else_body

            result_html = ""
            for item in items:
                # Простая подстановка атрибутов словаря
                temp = loop_body
                for key, val in item.items():
                    temp = temp.replace(f'{{{{ item.{key} }}}}', str(val))
                result_html = temp + result_html  # Новые записи сверху
            return result_html

        content = loop_pattern.sub(loop_replacer, content)

        # 2. Обработка условий {% if var %}
        # Если переменная есть и правдива - показываем контент, иначе вырезаем
        if_pattern = re.compile(r'\{% if (\w+) %\}(.*?)\{% endif %\}', re.DOTALL)

        def if_replacer(match):
            var_name = match.group(1)
            inner_content = match.group(2)
            return inner_content if context.get(var_name) else ""

        content = if_pattern.sub(if_replacer, content)

        # 3. Подстановка простых переменных {{ var }}
        for key, value in context.items():
            if isinstance(value, (str, int, float)):
                content = content.replace(f'{{{{ {key} }}}}', str(value))

        return content


class ConverterService:
    """Сервис для логики конвертации."""

    @staticmethod
    def convert(category: str, value: float, from_unit: str, to_unit: str) -> Optional[float]:
        if category not in UNITS_CONFIG:
            return None

        # Специфичная логика для температур
        if category == 'temperature':
            return ConverterService._convert_temp(value, from_unit, to_unit)

        # Логика для линейных величин (через базовую единицу)
        units = UNITS_CONFIG[category]['units']
        if from_unit not in units or to_unit not in units:
            return None

        # Приводим к базовой единице (factor=1.0), затем к целевой
        base_value = value * units[from_unit]['factor']
        return base_value / units[to_unit]['factor']

    @staticmethod
    def _convert_temp(value: float, from_unit: str, to_unit: str) -> float:
        # Нормализация в Цельсий
        celsius = value
        if from_unit == 'f':
            celsius = (value - 32) * 5 / 9
        elif from_unit == 'k':
            celsius = value - 273.15

        # Конвертация из Цельсия
        if to_unit == 'c':
            return celsius
        elif to_unit == 'f':
            return (celsius * 9 / 5) + 32
        elif to_unit == 'k':
            return celsius + 273.15
        return celsius


class RequestHandler(BaseHTTPRequestHandler):
    # Храним историю в памяти (при перезапуске сервера она сотрется)
    # В продакшене здесь была бы БД
    request_history: List[Dict[str, str]] = []

    def log_message(self, format, *args):
        # Переопределяем стандартный метод логирования
        log_entry = "%s - - [%s] %s\n" % (
            self.client_address[0],
            self.log_date_time_string(),
            format % args
        )

        # Вывод в консоль (чтобы вы видели, что происходит)
        sys.stderr.write(log_entry)

        # Запись в файл (добавляем в конец файла)
        with open("responses.log", "a", encoding="utf-8") as f:
            f.write(log_entry)
    
    def _get_options_html(self, category: str, selected: str) -> str:
        """Генерация <option> тегов."""
        if category not in UNITS_CONFIG:
            return ""

        options = []
        for code, data in UNITS_CONFIG[category]['units'].items():
            is_selected = 'selected' if code == selected else ''
            options.append(f'<option value="{code}" {is_selected}>{data["name"]}</option>')
        return "\n".join(options)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        params = parse_qs(parsed_url.query)

        # Валидация категории
        category = params.get('category', ['length'])[0]
        if category not in UNITS_CONFIG:
            category = 'length'

        # Дефолтные значения селектов
        available_units = list(UNITS_CONFIG[category]['units'].keys())
        unit_from = available_units[0] if available_units else ''
        unit_to = available_units[1] if len(available_units) > 1 else unit_from

        context = {
            'current_cat': category,
            'amount': 100,  # Дефолтное значение
            'unit_from_options': self._get_options_html(category, unit_from),
            'unit_to_options': self._get_options_html(category, unit_to),
            'history': self.request_history,
            'result': '',  # Пусто при GET
        }

        self._send_response(context)

    def do_POST(self):
        content_len = int(self.headers.get('Content-Length', 0))
        post_body = self.rfile.read(content_len).decode('utf-8')
        data = parse_qs(post_body)

        # Извлекаем данные с безопасными дефолтами
        category = data.get('category', ['length'])[0]
        amount_str = data.get('amount', ['0'])[0]
        unit_from = data.get('unit_from', [''])[0]
        unit_to = data.get('unit_to', [''])[0]
        action = data.get('action', ['convert'])[0]

        context = {
            'current_cat': category,
            'amount': amount_str,
            'history': self.request_history,
            'result': '',
            'explanation': ''
        }

        # Обработка кнопки Swap (меняем местами from/to)
        if action == 'swap':
            unit_from, unit_to = unit_to, unit_from

        # Обработка конвертации
        elif action == 'convert':
            try:
                val = float(amount_str)
                res = ConverterService.convert(category, val, unit_from, unit_to)

                if res is not None:
                    # Форматирование: убираем лишние нули после точки
                    res_formatted = f"{res:.4f}".rstrip('0').rstrip('.')
                    context['result'] = res_formatted

                    # Формируем объяснение для UI
                    u_from_name = UNITS_CONFIG[category]['units'][unit_from]['name']
                    u_to_name = UNITS_CONFIG[category]['units'][unit_to]['name']
                    context['explanation'] = f"{amount_str} {u_from_name} = {res_formatted} {u_to_name}"

                    # Добавляем в историю
                    self.request_history.append({
                        'from_val': f"{amount_str} {u_from_name}",
                        'to_val': f"{res_formatted} {u_to_name}"
                    })
                    # Держим только последние 5 записей
                    if len(self.request_history) > 5:
                        self.request_history.pop(0)

            except ValueError:
                # Если пользователь ввел не число, просто игнорируем расчет
                pass

        # Обновляем опции с учетом возможного swap
        context['unit_from_options'] = self._get_options_html(category, unit_from)
        context['unit_to_options'] = self._get_options_html(category, unit_to)

        self._send_response(context)

    def _send_response(self, context: Dict[str, Any]):
        engine = TemplateEngine('index_dynamic.html')
        html = engine.render(context)

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))


def run(server_class=HTTPServer, handler_class=RequestHandler, port=8000):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"Starting server on port {port}...")
    print(f"Open http://localhost:{port} in your browser.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == '__main__':
    run()