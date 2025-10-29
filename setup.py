from setuptools import setup, find_packages

setup(
    name="shared_options",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        "pydantic>=2.0",
        "numpy>=1.25",
        "pandas>=2.0"
    ],
    python_requires=">=3.9",
    description="Shared Option Features and Utilities for ML Pipelines",
    author="Davis Kim",
)
