"""STKDE"""

import numpy as np
import pandas as pd
import datetime
from sklearn.model_selection import train_test_split

import seaborn as sb
import matplotlib as mpl
import matplotlib.pyplot as plt

import geopandas as gpd
from shapely.geometry import Point

from statsmodels.nonparametric.kernel_density import KDEMultivariate

from sodapy import Socrata
import credentials as cre


# Observaciones
#
# 1. 3575 Incidents
# Training data 2926 incidents (January 1st - October 31st)
# Testing data 649 incidents (November 1st - December 31st)
#
# 2. Se requiere que la muestra sea "estable" en el periodo analizado

class STKDE:
    """STKDE class for a spatio-temporal kernel density estimation"""

    def __init__(self,
                 n: int = 1000,
                 year: str = "2017",
                 t_model: bool = False):
        """
        t_model: Debe ser True si se van a utilizar los métodos
        contour_plot o heatmap (calcula los bandwidths)
        """
        self.data = []

        self.training_data = []  # 3000
        self.testing_data = []  # 600
        self.n = n
        self.year = year

        self.get_data()

        self.x = np.array(self.training_data[['x']])
        self.y = np.array(self.training_data[['y']])
        self.t = np.array(self.training_data[['date_ordinal']])

        if t_model:
            # III) Testeo

            print("\nBuilding KDE...")

            self.kde = KDEMultivariate(data=[self.x, self.y, self.t],
                                       var_type='ccc',
                                       bw='cv_ml')

            hx, hy, ht = self.kde.bw

            print(f"\nOptimal Bandwidths: \n\n"
                  f"hx = {round(hx, 3)} ft\n"
                  f"hy = {round(hy, 3)} ft\n"
                  f"ht = {round(ht, 3)} days")

    def get_data(self):
        """Requests data using the Socrata API and saves in the
        self.data variable

        I) Reducción del Tamaño de la DB                            √
        II) Dividir training y testing data
        III) Usar training data para los cálculos de bandwidths
        IV) Testeo.
        """

        print("\nRequesting data...")

        with Socrata(cre.socrata_domain,
                     cre.API_KEY_S,
                     username=cre.USERNAME_S,
                     password=cre.PASSWORD_S) as client:
            query = \
                f"""
                select
                    incidentnum,
                    year1,
                    date1,
                    time1,
                    x_coordinate,
                    y_cordinate,
                    offincident
                where
                    year1 = {self.year}
                    and date1 is not null
                    and time1 is not null
                    and x_coordinate is not null
                    and y_cordinate is not null
                    and offincident = 'BURGLARY OF HABITATION - FORCED ENTRY'
                order by date1
                limit
                    {self.n}
                """  #  571000 max. 09/07/2019

            results = client.get(cre.socrata_dataset_identifier,
                                 query=query,
                                 content_type='json')

            pd.set_option('display.max_columns', None)
            pd.set_option('display.max_rows', None)

            df = pd.DataFrame.from_records(results)

            # DB Cleaning & Formatting

            df.loc[:, 'x_coordinate'] = df['x_coordinate'].apply(
                    lambda x: float(x))
            df.loc[:, 'y_cordinate'] = df['y_cordinate'].apply(
                    lambda x: float(x))
            df.loc[:, 'date1'] = df['date1'].apply(
                    lambda x: datetime.datetime.strptime(x.split('T')[0],
                                                         '%Y-%m-%d'))

            df = df[['x_coordinate', 'y_cordinate', 'date1']]
            df.loc[:, 'date_ordinal'] = df.apply(
                    lambda row: row.date1.toordinal(),
                    axis=1)

            df.rename(columns={'x_coordinate': 'x',
                               'y_cordinate': 'y',
                               'date1': 'date'},
                      inplace=True)

            # I) Reducción del tamaño de la DB

            df = df.sample(n=3600,
                           replace=False,
                           random_state=12504)  # Random Selection of rows

            df.sort_values(by=['date'], inplace=True)
            df.reset_index(drop=True, inplace=True)

            self.data = df

            # II) División en training y testing data

            self.training_data, self.testing_data = \
                train_test_split(self.data,
                                 test_size=1 / 6,
                                 shuffle=False)

            # print(self.training_data, "\n", self.testing_data)

            print("\n"
                  f"n = {self.n} incidents requested  Year = {self.year}"
                  "\n"
                  f"{self.data.shape[0]} incidents successfully retrieved")

    def data_barplot(self,
                     pdf: bool = False):
        """Bar Plot"""

        fig = plt.figure()
        ax = fig.add_subplot()
        ax.tick_params(axis='x', length=0)

        for i in range(1, 13):

            count = self.data[
                (self.data["date"].apply(lambda x: x.month) == i)
            ].shape[0]

            plt.bar(x=i, height=count, width=0.25, color=["black"])
            plt.text(x=i - 0.275, y=count + 5, s=str(count))

        plt.xticks(
                [i for i in range(1, 13)],
                [datetime.datetime.strptime(str(i), "%m").strftime('%b')
                 for i in range(1, 13)]
        )

        sb.despine()

        plt.xlabel("Month",
                   fontdict={'fontsize': 12.5,
                             'fontweight': 'bold'},
                   labelpad=10
                   )
        plt.ylabel("Count",
                   fontdict={'fontsize': 12.5,
                             'fontweight': 'bold'},
                   labelpad=7.5
                   )

        if pdf:
            plt.savefig(f"barplot.pdf", format='pdf')

        plt.show()

    def contour_plot(self,
                     bins: int,
                     ti: int,
                     pdf: bool = False):
        """Draw the contour lines"""

        print("\nPlotting Contours...")

        df = self.data[['x', 'y']]

        dallas = gpd.read_file('../Data/shapefiles/STREETS.shp')

        fig, ax = plt.subplots(figsize=(15, 15))
        ax.set_facecolor('xkcd:black')

        dallas.plot(ax=ax, alpha=.4, color="gray")
        # print(dallas.crs) # US Survey Foot: 0.3048 m

        geometry = [Point(xy) for xy in zip(df['x'], df['y'])]
        geo_df = gpd.GeoDataFrame(df,
                                  crs=dallas.crs,
                                  geometry=geometry)

        ax.set_facecolor('xkcd:black')

        dallas.plot(ax=ax,
                    alpha=.4,
                    color="gray",
                    zorder=1)
        geo_df.plot(ax=ax,
                    markersize=10,
                    color='red',
                    marker='o',
                    label='Incident',
                    zorder=2)
        plt.legend(prop={'size': 15})

        x, y = np.mgrid[
               self.x.min():self.x.max():bins * 1j,
               self.y.min():self.y.max():bins * 1j
               ]
        z = self.kde.pdf(np.vstack([x.flatten(),
                                    y.flatten(),
                                    ti * np.ones(x.size)]))
        # z_2 = z * 3000 * (10 ** 6) / (.304) # P. Elwin

        contourplot = plt.contour(x, y, z.reshape(x.shape),
                                  cmap='jet',
                                  zorder=3)

        plt.title(f"Dallas Incidents - Contourplot\n"
                  f"n = {self.data.shape[0]}    Year = {self.year}",
                  fontdict={'fontsize': 15,
                            'fontweight': 'bold'},
                  pad=20)
        plt.colorbar(contourplot,
                     ax=ax,
                     shrink=.4,
                     aspect=10)

        if pdf:
            plt.savefig("dallas_contourplot.pdf", format='pdf')
        plt.show()

    def heatmap(self,
                bins: int,
                ti: int,
                pdf: bool = False):
        """Plots the heatmap associated to a given t_i"""

        print("\nPlotting Heatmap...")

        dallas = gpd.read_file('../Data/shapefiles/STREETS.shp')

        fig, ax = plt.subplots(figsize=(15, 15))
        ax.set_facecolor('xkcd:black')

        dallas.plot(ax=ax,
                    alpha=.4,  # Ancho de las calles en este plot
                    color="gray",
                    zorder=1)

        x, y = np.mgrid[
               self.x.min():self.x.max():bins * 1j,
               self.y.min():self.y.max():bins * 1j
               ]

        z = self.kde.pdf(np.vstack([x.flatten(),
                                    y.flatten(),
                                    ti * np.ones(x.size)]))
        # z = np.ma.masked_array(z, z < .1e-11)

        heatmap = plt.pcolormesh(x, y, z.reshape(x.shape),
                                 shading='gouraud',
                                 alpha=.2,
                                 cmap=mpl.cm.get_cmap("jet"),
                                 zorder=2)

        plt.title(f"Dallas Incidents - Heatmap\n"
                  f"n = {self.data.shape[0]}   Year = {self.year}",
                  fontdict={'fontsize': 15,
                            'fontweight': 'bold'},
                  pad=20)
        cbar = plt.colorbar(heatmap,
                            ax=ax,
                            shrink=.4,
                            aspect=10)
        cbar.solids.set(alpha=1)

        if pdf:
            plt.savefig("dallas_heatmap.pdf", format='pdf')
        plt.show()


# Data
#
# 2012 - 58     incidents
# 2013 - 186    incidents
# 2014 - 54985  incidents
# 2015 - 94923  incidents   √
# 2016 - 102132 incidents   √
# 2017 - 96411  incidents   √
# 2018 - 98477  incidents
# 2019 - 64380  incidents (y creciendo)

if __name__ == "__main__":

    dallas_stkde = STKDE(n=150000,
                         year="2016",
                         t_model=False)
    dallas_stkde.data_barplot()
    # dallas_stkde.heatmap(bins=100,
    #                      ti=735234)
    # dallas_stkde.contour_plot(bins=1000,
    #                           ti=735234)
