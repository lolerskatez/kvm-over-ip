#!/usr/bin/env python
"""List all registered routes in the app."""
from app import create_app

app = create_app()
routes = sorted(set([str(rule) for rule in app.url_map.iter_rules()]))
print(f'Total routes: {len(routes)}\n')
for route in routes:
    print(route)
