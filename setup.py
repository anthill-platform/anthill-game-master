
from setuptools import setup, find_namespace_packages

DEPENDENCIES = [
    "anthill-common>=0.2.5",
    "python-geoip-python3==1.3",
    "python-geoip-geolite2-yplan",
]

setup(
    name='anthill-game-master',
    package_data={
      "anthill.game.master": ["anthill/game/master/sql", "anthill/game/master/static"]
    },
    version='0.2.3',
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
