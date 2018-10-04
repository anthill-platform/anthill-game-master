
from setuptools import setup, find_packages

DEPENDENCIES = [
    "anthill-common"
]

setup(
    name='anthill-game-master',
    package_data={
      "anthill.game.master": ["anthill/game/master/sql", "anthill/game/master/static"]
    },
    setup_requires=["pypigit-version"],
    git_version="0.1.0",
    description='Game servers hosting & matchmaking service for Anthill platform',
    author='desertkun',
    license='MIT',
    author_email='desertkun@gmail.com',
    url='https://github.com/anthill-platform/anthill-game-master',
    namespace_packages=["anthill"],
    include_package_data=True,
    packages=find_packages(),
    zip_safe=False,
    install_requires=DEPENDENCIES
)
