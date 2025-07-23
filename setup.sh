#!/bin/bash
"""
Setup script for CoinEx Trading Bot
Creates virtual environment and installs dependencies
"""

set -e  # Exit on any error

echo "🚀 CoinEx Trading Bot Setup"
echo "=========================="

# Check if we're already in a virtual environment
if [[ "$VIRTUAL_ENV" != "" ]]; then
    echo "✅ Already in virtual environment: $VIRTUAL_ENV"
else
    echo "📦 Creating virtual environment..."
    
    # Create virtual environment if it doesn't exist
    if [ ! -d "venv" ]; then
        python3 -m venv venv
        echo "✅ Virtual environment created"
    else
        echo "✅ Virtual environment already exists"
    fi
    
    # Activate virtual environment
    echo "🔄 Activating virtual environment..."
    source venv/bin/activate
    echo "✅ Virtual environment activated"
fi

# Upgrade pip
echo "📦 Upgrading pip..."
python -m pip install --upgrade pip

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "✅ Setup complete!"
echo ""
echo "🔧 Next steps:"
echo "1. Ensure your CoinEx API credentials are in environment variables:"
echo "   export COINEX_API_KEY='your_coinex_access_id'"
echo "   export COINEX_API_SECRET='your_coinex_secret_key'"
echo "   (Note: COINEX_API_KEY contains your CoinEx Access ID)"
echo ""
echo "2. Or create a .env file with your credentials:"
echo "   cp .env.example .env"
echo "   # Edit .env with your credentials"
echo ""
echo "3. Run the test:"
echo "   python test_runner.py"
echo ""
echo "📚 For detailed setup instructions, see TEST_SETUP.md"