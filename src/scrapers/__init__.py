"""訂房平台爬蟲套件。"""

from src.scrapers.agoda import AgodaScraper
from src.scrapers.base import BaseHotelScraper, run_scrapers
from src.scrapers.booking import BookingScraper
from src.scrapers.demo_hotel import DemoHotelScraper
from src.scrapers.trip_com import TripComScraper

__all__ = ["AgodaScraper", "BaseHotelScraper", "BookingScraper", "DemoHotelScraper", "TripComScraper", "run_scrapers"]
