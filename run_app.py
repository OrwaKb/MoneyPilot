"""PyInstaller entry point. The bundle can't run `-m app`, so this thin script
is the frozen exe's start: it just calls the same main() the module uses."""
from app.__main__ import main

if __name__ == "__main__":
    main()
