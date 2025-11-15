"""Setup script for Bond CLI."""
from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
  long_description = fh.read()

setup(
  name="bond-cli",
  version="0.1.0",
  author="Bond Contributors",
  author_email="hello@bondstreams.io",
  description="Conversational CLI for Bond real-time data streams",
  long_description=long_description,
  long_description_content_type="text/markdown",
  url="https://github.com/yourusername/bond",
  py_modules=["bond_cli"],
  python_requires=">=3.11",
  install_requires=[
    "httpx>=0.25.0",
    "websockets>=12.0",
    "rich>=13.0.0",
  ],
  entry_points={
    "console_scripts": [
      "bond=bond_cli:main",
    ],
  },
  classifiers=[
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "Topic :: Software Development :: Libraries",
    "License :: OSI Approved :: Apache Software License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
  ],
)
