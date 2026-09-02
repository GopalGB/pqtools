from pathlib import Path

from setuptools import find_packages, setup

README = Path(__file__).with_name("README.md").read_text(encoding="utf-8")

setup(
    name="mquery-toolkit",
    version="0.1.0",
    description="Unofficial offline tooling for Power Query M source",
    long_description=README,
    long_description_content_type="text/markdown",
    license="MIT",
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={"mquery_toolkit": ["_bridge.cjs", "THIRD_PARTY_NOTICES.txt"]},
    python_requires=">=3.11",
    entry_points={"console_scripts": ["mquery=mquery_toolkit.cli:main"]},
    extras_require={"fabric": ["pyarrow>=14"]},
)
