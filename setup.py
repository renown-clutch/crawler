from setuptools import find_packages, setup

setup(
    name="clutch-crawler",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "playwright>=1.40.0",
        "python-telegram-bot>=20.7",
        "apscheduler>=3.10.4",
    ],
    python_requires=">=3.13",
)
