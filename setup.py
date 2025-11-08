from setuptools import setup, find_packages

setup(
    name="shared_options",
    version="0.1.0",
    packages=find_packages(),  # automatically finds all packages and subpackages
    install_requires=[
        "pydantic>=2.0",
        "numpy>=1.25",
        "pandas>=2.0",
        "cryptography>=41.0",
        # add any other runtime dependencies here
    ],
    python_requires=">=3.8",
    description="Shared Option Features and Utilities",
    author="Davis Kim",
)
