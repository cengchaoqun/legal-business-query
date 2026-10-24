"""显式初始化全新的演示分析schema，不覆盖现有数据，不创建账号或授予权限。"""
import json
from pathlib import Path
import re

from sqlalchemy import create_engine, inspect, text


def initialize(database_url: str, fixture_dir: Path | str, watermark: str):
    directory = Path(fixture_dir)
    schema = json.loads((directory / 'schema.json').read_text('utf-8'))
    fixture = json.loads((directory / 'fixture_rows.json').read_text('utf-8'))
    if not watermark:
        raise ValueError('必须显式传入本批数据的同步水位')
    engine = create_engine(database_url, hide_parameters=True)
    try:
        inspector = inspect(engine)
        if inspector.get_table_names() or inspector.get_view_names():
            raise ValueError('仅允许初始化空的独立分析schema')
        with engine.begin() as conn:
            for view, metadata in schema['tables'].items():
                if not re.fullmatch(r'v_[a-z_]+', view):
                    raise ValueError('非法演示对象名称')
                table = 'demo_' + view[2:]
                columns = list(metadata['columns'])
                definitions = []
                for name, description in metadata['columns'].items():
                    match = re.match(r'(INTEGER|DATETIME|VARCHAR\(\d+\)|DECIMAL\(\d+,\d+\))', description)
                    if not match or not re.fullmatch(r'[a-z_]+', name):
                        raise ValueError('非法演示字段')
                    definitions.append(f'`{name}` {match.group(1)}')
                primary = ['case_id', 'lawyer_id'] if view == 'v_case_lawyer' else [columns[0]]
                definitions.append('PRIMARY KEY (' + ','.join(f'`{x}`' for x in primary) + ')')
                conn.exec_driver_sql(f'CREATE TABLE `{table}` (' + ','.join(definitions) + ')')
                placeholders = ','.join(':' + col for col in columns)
                insert = text(f'INSERT INTO `{table}` (' + ','.join(f'`{c}`' for c in columns) + f') VALUES ({placeholders})')
                records = [dict(zip(columns, row)) for row in fixture[view]]
                if records:
                    conn.execute(insert, records)
                security = 'SQL SECURITY DEFINER ' if conn.dialect.name == 'mysql' else ''
                conn.exec_driver_sql(f'CREATE {security}VIEW `{view}` AS SELECT ' + ','.join(f'`{c}`' for c in columns) + f' FROM `{table}`')
                for col in columns:
                    if col.endswith('_at') or (col.endswith('_id') and col not in primary):
                        conn.exec_driver_sql(f'CREATE INDEX `idx_{table}_{col}` ON `{table}` (`{col}`)')
            conn.exec_driver_sql('CREATE TABLE sync_watermark (scope_id VARCHAR(64) PRIMARY KEY, data_watermark VARCHAR(64) NOT NULL)')
            conn.execute(text('INSERT INTO sync_watermark(scope_id,data_watermark) VALUES (:scope,:watermark)'), {'scope': 'demo', 'watermark': watermark})
    finally:
        engine.dispose()
