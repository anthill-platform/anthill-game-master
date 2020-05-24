
from setuptools import setup, find_namespace_packages

DEPENDENCIES = [
    "anthill-common>=0.2.4"
]

setup(
    name='anthill-game-master',
    package_data={
      "anthill.game.master": ["anthill/game/master/sql", "anthill/game/master/static"]
    },
    version='0.2',
    description='Game servers hosting & matchmaking service for Anthill platform',
    author='desertkun',
    license='MIT',
    author_email='desertkun@gmail.com',
    url='https://github.com/anthill-platform/anthill-game-master',
    namespace_packages=["anthill"],
    include_package_data=True,
    packages=find_namespace_packages(include=["anthill.*"]),
    zip_safe=False,
    install_requires=DEPENDENCIES
)
