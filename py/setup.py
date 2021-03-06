
import setuptools

setuptools.setup(name='choochoo',
                 packages=setuptools.find_packages(),
                 version='0.35.0',
                 author='andrew cooke',
                 author_email='andrew@acooke.org',
                 description='Data Science for Training',
                 url='https://github.com/andrewcooke/choochoo',
                 long_description='''
# Choochoo (ch2)

An **open**, **hackable** and **free** training diary.

See [documentation](https://andrewcooke.github.io/choochoo/) for full
details.

Source and screenshots on [github](https://github.com/andrewcooke/choochoo).
                 ''',
                 long_description_content_type='text/markdown',
                 include_package_data=True,
                 install_requires=[
                     'bokeh',
                     'cachetools',
                     'colorama',
                     'colorlog',
                     'jupyter',
                     'matplotlib',
                     'numpy',
                     'openpyxl',
                     'pandas==0.25.3',
                     'pendulum',
                     'psutil',
                     'psycopg2',
                     'pyGeoTile',
                     'pyproj',
                     'rasterio',
                     'requests',
                     'scipy',
                     'sklearn',
                     'sqlalchemy-utils',
                     'sqlalchemy',
                     'sqlalchemy-batch-inserts',
                     'uritools',
                     'werkzeug',
                     ],
                 entry_points={
                     'console_scripts': [
                         'ch2 = ch2:main',
                     ],
                 },
                 classifiers=(
                     "Programming Language :: Python :: 3.7",
                     "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
                     "Operating System :: OS Independent",
                     "Development Status :: 4 - Beta",
                 ),
                 )

