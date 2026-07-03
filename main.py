from src.app import NautilusTraderApp
from src.args import parse_args


if __name__ == "__main__":
    NautilusTraderApp.from_args(parse_args()).run()
