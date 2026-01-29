from setuptools import setup, find_packages

setup(
    name="knightmind",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "fastapi",
        "uvicorn[standard]",
        "pydantic",
        "httpx",
        "chess",
        "stockfish",
    ],
)
