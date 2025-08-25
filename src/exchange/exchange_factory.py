"""
Exchange Factory
Creates appropriate exchange client based on configuration
"""
from src.exchange.coinex_client import CoinExClient
from src.utils.logger import get_logger
from config.settings import is_test_mode

logger = get_logger(__name__)


class ExchangeFactory:
    """Factory for creating exchange clients"""
    
    @staticmethod
    def create_exchange():
        """Create the appropriate exchange client"""
        logger.info("Creating CoinEx exchange client")
        return CoinExClient()
    
    @staticmethod
    def is_testing_mode():
        """Check if we're in testing mode"""
        return is_test_mode()