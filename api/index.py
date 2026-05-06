import sys
import os

# Adiciona o backend ao path para imports funcionarem no Vercel
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from main import app
