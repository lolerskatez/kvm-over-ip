"""Entry point for KVM-over-IP application."""

import os
import logging
from app import create_app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

if __name__ == '__main__':
    app = create_app()
    app.run(debug=os.environ.get('FLASK_ENV') == 'development')
