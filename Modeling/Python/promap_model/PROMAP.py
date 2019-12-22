"""PROMAP"""

import numpy as np
import pandas as pd
from time import time
import datetime

import seaborn as sb
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

import geopandas as gpd
from shapely.geometry import Point, shape
import fiona

# from pyevtk.hl import gridToVTK
# from paraview.simple import *

from statsmodels.nonparametric.kernel_density import KDEMultivariate, \
    EstimatorSettings

from sodapy import Socrata
import credentials as cre
import params
import auxiliar_functions as aux
from collections import defaultdict


# Observaciones
#
# 1. 3575 Incidents
# Training data 2926 incidents (January 1st - October 31st)
# Testing data 649 incidents (November 1st - December 31st)
#
# 2. Se requiere que la muestra sea "estable" en el periodo analizado

def _time(fn):
    def inner_1(*args, **kwargs):
        start = time()

        fn(*args, **kwargs)

        print(f"\nFinished in {round(time() - start, 3)} sec")

    return inner_1


def checked_points(points):
    dallas_shp = gpd.read_file('../../Data/Councils/Councils.shp')

    df_points = pd.DataFrame(
        {'x': points[0, :], 'y': points[1, :], 't': points[2, :]}
    )

    inc_points = df_points[['x', 'y']].apply(lambda row:
                                             Point(row['x'], row['y']),
                                             axis=1)
    geo_inc = gpd.GeoDataFrame({'geometry': inc_points, 'day': df_points['t']})

    # Para borrar el warning asociado a != epsg distintos
    geo_inc.crs = {'init': 'epsg:2276'}

    valid_inc = gpd.tools.sjoin(geo_inc,
                                dallas_shp,
                                how='inner',
                                op='intersects').reset_index()

    valid_inc_2 = valid_inc[['geometry', 'day']]

    x = valid_inc_2['geometry'].apply(lambda row: row.x)
    y = valid_inc_2['geometry'].apply(lambda row: row.y)
    t = valid_inc_2['day']

    v_points = np.array([x, y, t])

    return v_points


# settings = EstimatorSettings(efficient=True,
#                              n_jobs=4)


class MyKDEMultivariate(KDEMultivariate):
    def resample(self, size: int):
        print("\nResampling...", end=" ")

        n, d = self.data.shape
        indices = np.random.randint(0, n, size)

        cov = np.diag(self.bw) ** 2
        means = self.data[indices, :]
        norm = np.random.multivariate_normal(np.zeros(d), cov, size)

        # simulated and checked points
        s_points = np.transpose(means + norm)
        c_points = checked_points(s_points)

        print(f"\n{size - c_points.shape[1]} invalid points found")

        if size == c_points.shape[1]:
            print("\nfinished!")
            return s_points

        a_points = self.resample(size - c_points.shape[1])

        return np.hstack((c_points, a_points))


class Promap:
    """
    Class for a spatio-temporal PROMAP density estimation
    """

    def __init__(self,
                 n: int = 1000,
                 year: str = "2017",
                 bw=None):
        """
        n: Número de registros que se piden a la database.

        year: Año de los registros pedidos

        t_model: Entrenamiento del modelo, True en caso de que se quieran
        usar los métodos contour_plot o heatmap.
        """

        self.data = []
        self.training_data = []  # 3000
        self.testing_data = []  # 600

        self.bw_x = params.bw['x']
        self.bw_y = params.bw['y']

        # self.predict_groups = params.predict_groups

        self.n = n
        self.year = year

        self.get_data()

        # esto le pasa los datos al KDE
        # self.kde = KDEMultivariate(
        #         [np.array(self.training_data[['x']]),
        #          np.array(self.training_data[['y']]),
        #          np.array(self.training_data[['y_day']])],
        #         'ccc')

        # print(f'\n SELF TRAINING DATA {self.training_data}')

    @_time
    def get_data(self):
        """
        Requests data using the Socrata API and saves in the
        self.data variable
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
                lambda x: datetime.datetime.strptime(
                    x.split('T')[0], '%Y-%m-%d')
            )

            df = df[['x_coordinate', 'y_cordinate', 'date1']]
            df.loc[:, 'y_day'] = df["date1"].apply(
                lambda x: x.timetuple().tm_yday
            )

            df.rename(columns={'x_coordinate': 'x',
                               'y_cordinate': 'y',
                               'date1': 'date'},
                      inplace=True)

            # Reducción del tamaño de la DB

            df = df.sample(n=3600,
                           replace=False,
                           random_state=250499)

            df.sort_values(by=['date'], inplace=True)
            df.reset_index(drop=True, inplace=True)

            self.data = df

            # Hasta este punto tenemos los datos en un formato que no nos
            # srve, ahora se pasaran a un formato (X,Y)

            geometry = [Point(xy) for xy in zip(
                np.array(self.data[['x']]),
                np.array(self.data[['y']]))
                        ]

            self.geo_data = gpd.GeoDataFrame(self.data,  # gdf de incidentes
                                             crs=2276,
                                             geometry=geometry)

            self.geo_data.to_crs(epsg=3857, inplace=True)

            # print(self.geo_data.geometry)

            # Ahora debemos juntar los datos del geo data y los day_y

            data = defaultdict(list)

            for i in range(len(self.data)):
                data['x'].append(self.geo_data.geometry[i].x)
                data['y'].append(self.geo_data.geometry[i].y)
                data['date'].append(self.data['date'][i])
                data['y_day'].append(self.data['y_day'][i])

            self.data_ok = pd.DataFrame(data=data)

            self.data = self.data_ok
            print('--- DATA ---\n', self.data)


            # División en training y testing data

            self.training_data = self.data[
                self.data["date"].apply(lambda x: x.month) <= 10
                ]

            print('--- Training Data ---\n', self.training_data)

            self.testing_data = self.data[
                self.data["date"].apply(lambda x: x.month) > 10
                ]

            print('--- Testing Data ---\n', self.testing_data)

            # Time 1 Data for building STKDE models : 1 Month

            # for group in self.predict_groups:
            #     self.predict_groups[group]['t1_data'] = \
            #         self.data[
            #             self.data['date'].apply(
            #                     lambda x:
            #                     self.predict_groups[group]['t1_data'][0]
            #                     <= x.date() <=
            #                     self.predict_groups[group]['t1_data'][-1]
            #             )
            #         ]
            #
            # # Time 2 Data for Prediction            : 1 Week
            #
            # for group in self.predict_groups:
            #     self.predict_groups[group]['t2_data'] = \
            #         self.data[
            #             self.data['date'].apply(
            #                     lambda x:
            #                     self.predict_groups[group]['t2_data'][0]
            #                     <= x.date() <=
            #                     self.predict_groups[group]['t2_data'][-1]
            #             )
            #         ]

            # print(self.predict_groups['group_8']['t2_data'].shape[0])
            # print(self.predict_groups['group_8']['t2_data'].tail())

            print("\n"
                  f"\tn = {self.n} incidents requested  Year = {self.year}"
                  "\n"
                  f"\t{self.data.shape[0]} incidents successfully retrieved!")

    @_time
    def train_model(self, x, y, t, bw=None):
        """
        Entrena el modelo y genera un KDE

        bw: Si es un arreglo, este debe contener los bandwidths
        dados por el usuario
        """

        print("\nBuilding KDE...")

        if bw is not None:
            print(f"\n\tGiven Bandwidths: \n"
                  f"\t\thx = {round(bw[0], 3)} ft\n"
                  f"\t\thy = {round(bw[1], 3)} ft\n"
                  f"\t\tht = {round(bw[2], 3)} days")

            for group in self.predict_groups:
                self.predict_groups[group]['STKDE'] = \
                    MyKDEMultivariate(
                        data=[
                            np.array(self.predict_groups[group]['t1_data'][
                                         ['x']]),
                            np.array(self.predict_groups[group]['t1_data'][
                                         ['y']]),
                            np.array(self.predict_groups[group]['t1_data'][
                                         ['y_day']])
                        ],
                        var_type='ccc',
                        bw=bw)

        else:
            print(2222)
            self.kde = KDEMultivariate(data=[x, y, t],
                                       var_type='ccc',
                                       bw='cv_ml')

            print(f"\n\tOptimal Bandwidths: \n"
                  f"\t\thx = {round(self.kde.bw[0], 3)} ft\n"
                  f"\t\thy = {round(self.kde.bw[1], 3)} ft\n"
                  f"\t\tht = {round(self.kde.bw[2], 3)} days")

    @_time
    def data_barplot(self,
                     pdf: bool = False):
        """
        Bar Plot

        pdf: True si se desea guardar el plot en formato pdf
        """

        print("\nPlotting Bar Plot...")

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
            plt.savefig(f"output/barplot.pdf", format='pdf')

        plt.show()

    @_time
    def calcular_densidades(self):

        celdas_pintadas = 0
        delitos = 0

        # xx, yy = np.mgrid[self.x_min + self.hx / 2:self.x_max - self.hx /
        #                                            2:self.bins *
        #                                              1j,
        #          self.y_min + self.hy / 2:self.y_max - self.hy / 2:self.bins *
        #                                                            1j]

        matriz_con_ceros = np.zeros((self.bins, self.bins))

        for k in range(len(self.training_data)):
            delitos += 1
            print('N =', delitos)
            x, y, t = self.training_data['x'][k], self.training_data['y'][k], \
                      self.training_data['y_day'][k]
            for i in range(self.bins):
                for j in range(self.bins):
                    elemento_x = self.x[i][0]
                    elemento_y = self.y[0][j]
                    # print(f'Punto Actual x: {elemento_x}, y: {elemento_y}')
                    time_weight = 1 / aux.n_semanas(self.total_dias, t)
                    if aux.linear_distance(elemento_x, x) > self.bw_x or \
                            aux.linear_distance(
                                elemento_y, y) > self.bw_y:
                        # print(
                        #    'Esta celda está fuera del limite del ancho de
                        #    banda')
                        cell_weight = 0
                        pass
                    else:
                        cell_weight = 1 / aux.cells_distance(x, y, elemento_x,
                                                             elemento_y,
                                                             self.hx,
                                                             self.hy)
                        celdas_pintadas += 1

                        # print('SUMANDO PESO')
                    # print('CELL WIGHT: ', cell_weight)
                    # print()
                    matriz_con_ceros[i][j] += time_weight * cell_weight

        print('- CELDAS PINTADAS:', celdas_pintadas)
        print('- DIAS TOTALES DE TRAINING:', self.total_dias)
        plt.imshow(np.flipud(matriz_con_ceros.T),
                   extent=[self.x_min, self.x_max, self.y_min, self.y_max])
        plt.colorbar()
        plt.show()

    def generar_df(self):

        print("\nGenerando dataframe...\n")

        print("\tCreando Mgrid...")

        self.x_min = params.dallas_limits['x_min']
        self.x_max = params.dallas_limits['x_max']
        self.y_min = params.dallas_limits['y_min']
        self.y_max = params.dallas_limits['y_max']
        #
        # self.x_min = self.training_data['x'].min()
        # self.x_max = self.training_data['x'].max()
        # self.y_min = self.training_data['y'].min()
        # self.y_max = self.training_data['y'].max()

        print('valores', self.x_min, self.x_max, self.y_min, self.y_max)

        self.bins = params.bins

        self.hx = abs(self.x_max - self.x_min) / self.bins
        self.hy = abs(self.y_max - self.y_min) / self.bins

        print('xmax', self.x_max)
        print('xmin', self.x_min)

        print('hx', abs(self.x_max - self.x_min))
        print('hy', self.hy)

        self.x, self.y = np.mgrid[self.x_min + self.hx / 2:self.x_max -
                                                           self.hx /
                                                           2:self.bins *
                                                             1j,
                         self.y_min + self.hy / 2:self.y_max - self.hy / 2:self.bins *
                                                                           1j]

        self.total_dias = self.training_data['y_day'].max()


        # geometry = [Point(xy) for xy in zip(
        #     np.array(self.data[['x']]),
        #     np.array(self.data[['y']]))
        #             ]
        # self.data = gpd.GeoDataFrame(self.data,  # gdf de incidentes
        #                              crs=2276,
        #                              geometry=geometry)
        #
        # self.data.to_crs(epsg=3857, inplace=True)

    # @_time

    # @timer

    # def spatial_pattern(self,
    #                     pdf: bool = False):
    #     """
    #     Spatial pattern of incidents
    #
    #     pdf: True si se desea guardar el plot en formato pdf
    #     """
    #
    #     print("\nPlotting Spatial Pattern of incidents...", sep="\n\n")
    #
    #     print("\tReading Streets...", end=" ")
    #     dallas_districts = gpd.GeoDataFrame.from_file(
    #             "../Data/Councils/Councils.shp")
    #     dallas = gpd.read_file('../Data/Streets/STREETS.shp')
    #     print("finished!")
    #
    #     fig, ax = plt.subplots(figsize=(15, 15))
    #     ax.set_facecolor('xkcd:black')
    #
    #     # US Survey Foot: 0.3048 m
    #     # print("\n", f"EPSG: {dallas.crs['init'].split(':')[1]}")  # 2276
    #
    #     geometry = [Point(xy) for xy in zip(
    #             np.array(self.testing_data[['x']]),
    #             np.array(self.testing_data[['y']]))
    #                 ]
    #     geo_df = gpd.GeoDataFrame(self.testing_data,
    #                               crs=dallas.crs,
    #                               geometry=geometry)
    #
    #     print("\tPlotting Districts...", end=" ")
    #
    #     handles = []
    #
    #     for district, data in dallas_districts.groupby('DISTRICT'):
    #         data.plot(ax=ax,
    #                   color=params.d_colors[district],
    #                   linewidth=2.5,
    #                   edgecolor="black")
    #         handles.append(mpatches.Patch(color=params.d_colors[district],
    #                                       label=f"Dallas District {district}"))
    #
    #     handles.sort(key=lambda x: int(x._label.split(' ')[2]))
    #     handles = [Line2D([], [], marker='o', color='red', label='Incident',
    #                       linestyle="None"),
    #                Line2D([0], [0], color="steelblue", label="Streets")] + \
    #               handles
    #
    #     print("finished!")
    #
    #     print("\tPlotting Streets...", end=" ")
    #     dallas.plot(ax=ax,
    #                 alpha=0.4,
    #                 color="steelblue",
    #                 zorder=2,
    #                 label="Streets")
    #     print("finished!")
    #
    #     print("\tPlotting Incidents...", end=" ")
    #
    #     geo_df.plot(ax=ax,
    #                 markersize=17.5,
    #                 color='red',
    #                 marker='o',
    #                 zorder=3,
    #                 label="Incidents")
    #
    #     print("finished!")
    #
    #     plt.title(f"Dallas Incidents - Spatial Pattern\n"
    #               f"{self.year}",
    #               fontdict={'fontsize': 20},
    #               pad=25)
    #
    #     plt.legend(loc="lower right",
    #                frameon=False,
    #                fontsize=13.5,
    #                handles=handles)
    #
    #     ax.set_axis_off()
    #     plt.show()
    #
    #     if pdf:
    #         plt.savefig("output/spatial_pattern.pdf", format='pdf')
    #     plt.show()

    # @timer
    # def contour_plot(self,
    #                  bins: int,
    #                  ti: int,
    #                  pdf: bool = False):
    #     """
    #     Draw the contour lines
    #
    #     bins:
    #
    #     ti:
    #
    #     pdf: True si se desea guardar el plot en formato pdf
    #     """
    #
    #     print("\nPlotting Contours...")
    #
    #     dallas = gpd.read_file('../Data/Streets/STREETS.shp')
    #
    #     fig, ax = plt.subplots(figsize=(15, 12))
    #     ax.set_facecolor('xkcd:black')
    #
    #     dallas.plot(ax=ax,
    #                 alpha=.4,
    #                 color="gray",
    #                 zorder=1)
    #
    #     x, y = np.mgrid[
    #            np.array(self.testing_data[['x']]).min():
    #            np.array(self.testing_data[['x']]).max():bins * 1j,
    #            np.array(self.testing_data[['y']]).min():
    #            np.array(self.testing_data[['y']]).max():bins * 1j
    #            ]
    #
    #     z = self.kde.pdf(np.vstack([x.flatten(),
    #                                 y.flatten(),
    #                                 ti * np.ones(x.size)]))
    #     # z_2 = z * 3000 * (10 ** 6) / (.304) # P. Elwin
    #
    #     contourplot = plt.contour(x, y, z.reshape(x.shape),
    #                               cmap='jet',
    #                               zorder=2)
    #
    #     plt.title(f"Dallas Incidents - Contourplot\n"
    #               f"n = {self.data.shape[0]}    Year = {self.year}",
    #               fontdict={'fontsize': 20},
    #               pad=20)
    #     plt.colorbar(contourplot,
    #                  ax=ax,
    #                  shrink=.4,
    #                  aspect=10)
    #
    #     if pdf:
    #         plt.savefig("output/dallas_contourplot.pdf", format='pdf')
    #     plt.show()
    #
    # @timer
    # def heatmap(self,
    #             bins: int,
    #             ti: int,
    #             pdf: bool = False):
    #     """
    #     Plots the heatmap associated to a given t_i
    #
    #     bins:
    #
    #     ti:
    #
    #     pdf:
    #     """
    #
    #     print("\nPlotting Heatmap...")
    #
    #     dallas = gpd.read_file('../Data/Streets/STREETS.shp')
    #
    #     fig, ax = plt.subplots(figsize=(15, 12))
    #     ax.set_facecolor('xkcd:black')
    #
    #     dallas.plot(ax=ax,
    #                 alpha=.4,  # Ancho de las calles
    #                 color="gray",
    #                 zorder=1)
    #
    #     x, y = np.mgrid[
    #            np.array(self.testing_data[['x']]).min():
    #            np.array(self.testing_data[['x']]).max():bins * 1j,
    #            np.array(self.testing_data[['y']]).min():
    #            np.array(self.testing_data[['y']]).max():bins * 1j
    #            ]
    #
    #     z = self.kde.pdf(np.vstack([x.flatten(),
    #                                 y.flatten(),
    #                                 ti * np.ones(x.size)]))
    #     # z = np.ma.masked_array(z, z < .1e-11)
    #
    #     heatmap = plt.pcolormesh(x, y, z.reshape(x.shape),
    #                              shading='gouraud',
    #                              alpha=.2,
    #                              cmap=mpl.cm.get_cmap("jet"),
    #                              zorder=2)
    #
    #     plt.title(f"Dallas Incidents - Heatmap\n"
    #               f"n = {self.data.shape[0]}   Year = {self.year}",
    #               fontdict={'fontsize': 20},
    #               pad=20)
    #     cbar = plt.colorbar(heatmap,
    #                         ax=ax,
    #                         shrink=.5,
    #                         aspect=10)
    #     cbar.solids.set(alpha=1)
    #     # ax.set_axis_off()
    #
    #     if pdf:
    #         plt.savefig("output/dallas_heatmap.pdf", format='pdf')
    #     plt.show()

    @_time
    def generate_grid(self,
                      bins: int = 100):
        """
        :return:
        """
        print("\nCreating 3D grid...")

        x, y, t = np.mgrid[
                  np.array(self.testing_data[['x']]).min():
                  np.array(self.testing_data[['x']]).max():bins * 1j,
                  np.array(self.testing_data[['y']]).min():
                  np.array(self.testing_data[['y']]).max():bins * 1j,
                  np.array(self.testing_data[['y_day']]).min():
                  np.array(self.testing_data[['y_day']]).max():60 * 1j
                  ]

        print(x.shape)

        print("\n\tEstimating densities...")

        d = dallas_stkde.kde.pdf(
            np.vstack([
                x.flatten(),
                y.flatten(),
                t.flatten()
            ])).reshape((100, 100, 60))

        print("\nExporting 3D grid...")

        # gridToVTK("STKDE grid",
        #           x, y, t,
        #           pointData={"density": d,
        #                      "y_day": t})

    # @timer
    # def plot_4d(self,
    #             jpg: bool = False,
    #             interactive: bool = False):
    #     """
    #     Docstring
    #     """
    #
    #     print("\nCreating 4D plot...")
    #
    #     # get the material library
    #     materialLibrary1 = GetMaterialLibrary()
    #
    #     # Create a new 'Render View'
    #     renderView1 = CreateView('RenderView')
    #     # renderView1.GetRenderWindow().SetFullScreen(True)
    #     renderView1.ViewSize = [1080, 720]
    #     renderView1.AxesGrid = 'GridAxes3DActor'
    #     renderView1.CenterOfRotation = [2505251.4078454673, 6981929.658190809,
    #                                     336000.0]
    #     renderView1.StereoType = 0
    #     renderView1.CameraPosition = [2672787.133709079, 6691303.18204621,
    #                                   510212.0148339354]
    #     renderView1.CameraFocalPoint = [2505211.707979299, 6981538.82059641,
    #                                     335389.1971413433]
    #     renderView1.CameraViewUp = [-0.23106610676072656, 0.40064135354217617,
    #                                 0.8866199637603102]
    #     renderView1.CameraParallelScale = 97832.66331727587
    #     renderView1.Background = [0.0, 0.0, 0.0]
    #     renderView1.OSPRayMaterialLibrary = materialLibrary1
    #
    #     # init the 'GridAxes3DActor' selected for 'AxesGrid'
    #     renderView1.AxesGrid.XTitle = 'X'
    #     renderView1.AxesGrid.YTitle = 'Y'
    #     renderView1.AxesGrid.ZTitle = 'T'
    #     renderView1.AxesGrid.XTitleFontFile = ''
    #     renderView1.AxesGrid.YTitleFontFile = ''
    #     renderView1.AxesGrid.ZTitleFontFile = ''
    #     renderView1.AxesGrid.XLabelFontFile = ''
    #     renderView1.AxesGrid.YLabelFontFile = ''
    #     renderView1.AxesGrid.ZLabelFontFile = ''
    #
    #     # ----------------------------------------------------------------
    #     # restore active view
    #     SetActiveView(renderView1)
    #     # ----------------------------------------------------------------
    #
    #     # ----------------------------------------------------------------
    #     # setup the data processing pipelines
    #     # ----------------------------------------------------------------
    #
    #     # Si no existe STKDE_grid.vts, crear malla (INCOMPLETO)
    #
    #     # create a new 'XML Structured Grid Reader'
    #
    #     print("\n\tLoading 3D grid...", end=" ")
    #
    #     densities = XMLStructuredGridReader(FileName=[
    #         '/Users/msmendozaelguera/Desktop/iPre/Modeling/Python/STKDE '
    #         'grid.vts'])
    #     densities.PointArrayStatus = ['density', 'y_day']
    #
    #     print("finished!")
    #
    #     # create a new 'GDAL Vector Reader'
    #     print("\tLoading Dallas Street shapefile...", end=" ")
    #
    #     dallasMap = GDALVectorReader(
    #             FileName='/Users/msmendozaelguera/Desktop/iPre/Modeling/Data'
    #                      '/Streets'
    #                      '/STREETS.shp')
    #
    #     print("finished!")
    #
    #     print("\tPlotting Contours...", end=" ")
    #
    #     # create a new 'Contour'
    #     aboveSurfaceContour = Contour(Input=densities)
    #     aboveSurfaceContour.ContourBy = ['POINTS', 'y_day']
    #     aboveSurfaceContour.Isosurfaces = [360.0]
    #     aboveSurfaceContour.PointMergeMethod = 'Uniform Binning'
    #
    #     # create a new 'Contour'
    #     aboveContour = Contour(Input=aboveSurfaceContour)
    #     aboveContour.ContourBy = ['POINTS', 'density']
    #     aboveContour.ComputeScalars = 1
    #     aboveContour.Isosurfaces = [1.2217417871681798e-13]
    #     aboveContour.PointMergeMethod = 'Uniform Binning'
    #
    #     # create a new 'Contour'
    #     belowSurfaceContour = Contour(Input=densities)
    #     belowSurfaceContour.ContourBy = ['POINTS', 'y_day']
    #     belowSurfaceContour.Isosurfaces = [307.0]
    #     belowSurfaceContour.PointMergeMethod = 'Uniform Binning'
    #
    #     # create a new 'Contour'
    #     belowContour = Contour(Input=belowSurfaceContour)
    #     belowContour.ContourBy = ['POINTS', 'density']
    #     belowContour.ComputeScalars = 1
    #     belowContour.Isosurfaces = [1.8379040711040815e-12]
    #     belowContour.PointMergeMethod = 'Uniform Binning'
    #
    #     print("finished!")
    #
    #     # ----------------------------------------------------------------
    #     # setup the visualization in view 'renderView1'
    #     # ----------------------------------------------------------------
    #
    #     print("\tRendering Volume...", end=" ")
    #
    #     # show data from densities
    #     densitiesDisplay = Show(densities, renderView1)
    #
    #     # get color transfer function/color map for 'density'
    #     densityLUT = GetColorTransferFunction('density')
    #     densityLUT.RGBPoints = [0.0, 0.278431372549, 0.278431372549,
    #                             0.858823529412,
    #                             5.418939399286293e-13, 0.0, 0.0, 0.360784313725,
    #                             1.0799984117458697e-12, 0.0, 1.0, 1.0,
    #                             1.625681819785888e-12, 0.0, 0.501960784314, 0.0,
    #                             2.1637862916031283e-12, 1.0, 1.0, 0.0,
    #                             2.7056802315317578e-12, 1.0, 0.380392156863,
    #                             0.0,
    #                             3.247574171460387e-12, 0.419607843137, 0.0, 0.0,
    #                             3.7894681113890166e-12, 0.878431372549,
    #                             0.301960784314,
    #                             0.301960784314]
    #     densityLUT.ColorSpace = 'RGB'
    #     densityLUT.ScalarRangeInitialized = 1.0
    #
    #     # get opacity transfer function/opacity map for 'density'
    #     densityPWF = GetOpacityTransferFunction('density')
    #     densityPWF.Points = [0.0, 0.0, 0.5, 0.0, 3.7894681113890166e-12, 1.0,
    #                          0.5, 0.0]
    #     densityPWF.ScalarRangeInitialized = 1
    #
    #     # trace defaults for the display properties.
    #     densitiesDisplay.Representation = 'Volume'
    #     densitiesDisplay.ColorArrayName = ['POINTS', 'density']
    #     densitiesDisplay.LookupTable = densityLUT
    #     densitiesDisplay.Scale = [1.0, 1.0, 1000.0]
    #     densitiesDisplay.OSPRayScaleArray = 'density'
    #     densitiesDisplay.OSPRayScaleFunction = 'PiecewiseFunction'
    #     densitiesDisplay.SelectOrientationVectors = 'None'
    #     densitiesDisplay.ScaleFactor = 13997.517379119061
    #     densitiesDisplay.SelectScaleArray = 'None'
    #     densitiesDisplay.GlyphType = 'Arrow'
    #     densitiesDisplay.GlyphTableIndexArray = 'None'
    #     densitiesDisplay.GaussianRadius = 699.875868955953
    #     densitiesDisplay.SetScaleArray = ['POINTS', 'density']
    #     densitiesDisplay.ScaleTransferFunction = 'PiecewiseFunction'
    #     densitiesDisplay.OpacityArray = ['POINTS', 'density']
    #     densitiesDisplay.OpacityTransferFunction = 'PiecewiseFunction'
    #     densitiesDisplay.DataAxesGrid = 'GridAxesRepresentation'
    #     densitiesDisplay.SelectionCellLabelFontFile = ''
    #     densitiesDisplay.SelectionPointLabelFontFile = ''
    #     densitiesDisplay.PolarAxes = 'PolarAxesRepresentation'
    #     densitiesDisplay.ScalarOpacityFunction = densityPWF
    #     densitiesDisplay.ScalarOpacityUnitDistance = 2272.3875215933476
    #
    #     # init the 'GridAxesRepresentation' selected for 'DataAxesGrid'
    #     densitiesDisplay.DataAxesGrid.XTitleFontFile = ''
    #     densitiesDisplay.DataAxesGrid.YTitleFontFile = ''
    #     densitiesDisplay.DataAxesGrid.ZTitleFontFile = ''
    #     densitiesDisplay.DataAxesGrid.XLabelFontFile = ''
    #     densitiesDisplay.DataAxesGrid.YLabelFontFile = ''
    #     densitiesDisplay.DataAxesGrid.ZLabelFontFile = ''
    #
    #     # init the 'PolarAxesRepresentation' selected for 'PolarAxes'
    #     densitiesDisplay.PolarAxes.Scale = [1.0, 1.0, 1000.0]
    #     densitiesDisplay.PolarAxes.PolarAxisTitleFontFile = ''
    #     densitiesDisplay.PolarAxes.PolarAxisLabelFontFile = ''
    #     densitiesDisplay.PolarAxes.LastRadialAxisTextFontFile = ''
    #     densitiesDisplay.PolarAxes.SecondaryRadialAxesTextFontFile = ''
    #
    #     print("finished!")
    #
    #     print("\tMaking some adjustments...", end=" ")
    #
    #     # show data from belowContour
    #     belowContourDisplay = Show(belowContour, renderView1)
    #
    #     # trace defaults for the display properties.
    #     belowContourDisplay.Representation = 'Wireframe'
    #     belowContourDisplay.ColorArrayName = ['POINTS', 'density']
    #     belowContourDisplay.LookupTable = densityLUT
    #     belowContourDisplay.LineWidth = 2.0
    #     belowContourDisplay.Scale = [1.0, 1.0, 1000.0]
    #     belowContourDisplay.OSPRayScaleArray = 'Normals'
    #     belowContourDisplay.OSPRayScaleFunction = 'PiecewiseFunction'
    #     belowContourDisplay.SelectOrientationVectors = 'None'
    #     belowContourDisplay.ScaleFactor = 7461.155641368963
    #     belowContourDisplay.SelectScaleArray = 'None'
    #     belowContourDisplay.GlyphType = 'Arrow'
    #     belowContourDisplay.GlyphTableIndexArray = 'None'
    #     belowContourDisplay.GaussianRadius = 373.05778206844815
    #     belowContourDisplay.SetScaleArray = ['POINTS', 'Normals']
    #     belowContourDisplay.ScaleTransferFunction = 'PiecewiseFunction'
    #     belowContourDisplay.OpacityArray = ['POINTS', 'Normals']
    #     belowContourDisplay.OpacityTransferFunction = 'PiecewiseFunction'
    #     belowContourDisplay.DataAxesGrid = 'GridAxesRepresentation'
    #     belowContourDisplay.SelectionCellLabelFontFile = ''
    #     belowContourDisplay.SelectionPointLabelFontFile = ''
    #     belowContourDisplay.PolarAxes = 'PolarAxesRepresentation'
    #
    #     # init the 'GridAxesRepresentation' selected for 'DataAxesGrid'
    #     belowContourDisplay.DataAxesGrid.XTitleFontFile = ''
    #     belowContourDisplay.DataAxesGrid.YTitleFontFile = ''
    #     belowContourDisplay.DataAxesGrid.ZTitleFontFile = ''
    #     belowContourDisplay.DataAxesGrid.XLabelFontFile = ''
    #     belowContourDisplay.DataAxesGrid.YLabelFontFile = ''
    #     belowContourDisplay.DataAxesGrid.ZLabelFontFile = ''
    #
    #     # init the 'PolarAxesRepresentation' selected for 'PolarAxes'
    #     belowContourDisplay.PolarAxes.Scale = [1.0, 1.0, 1000.0]
    #     belowContourDisplay.PolarAxes.PolarAxisTitleFontFile = ''
    #     belowContourDisplay.PolarAxes.PolarAxisLabelFontFile = ''
    #     belowContourDisplay.PolarAxes.LastRadialAxisTextFontFile = ''
    #     belowContourDisplay.PolarAxes.SecondaryRadialAxesTextFontFile = ''
    #
    #     # show data from aboveContour
    #     aboveContourDisplay = Show(aboveContour, renderView1)
    #
    #     # trace defaults for the display properties.
    #     aboveContourDisplay.Representation = 'Wireframe'
    #     aboveContourDisplay.ColorArrayName = ['POINTS', 'density']
    #     aboveContourDisplay.LookupTable = densityLUT
    #     aboveContourDisplay.LineWidth = 2.0
    #     aboveContourDisplay.Scale = [1.0, 1.0, 1000.0]
    #     aboveContourDisplay.OSPRayScaleArray = 'Normals'
    #     aboveContourDisplay.OSPRayScaleFunction = 'PiecewiseFunction'
    #     aboveContourDisplay.SelectOrientationVectors = 'None'
    #     aboveContourDisplay.ScaleFactor = 12060.679544949253
    #     aboveContourDisplay.SelectScaleArray = 'None'
    #     aboveContourDisplay.GlyphType = 'Arrow'
    #     aboveContourDisplay.GlyphTableIndexArray = 'None'
    #     aboveContourDisplay.GaussianRadius = 603.0339772474626
    #     aboveContourDisplay.SetScaleArray = ['POINTS', 'Normals']
    #     aboveContourDisplay.ScaleTransferFunction = 'PiecewiseFunction'
    #     aboveContourDisplay.OpacityArray = ['POINTS', 'Normals']
    #     aboveContourDisplay.OpacityTransferFunction = 'PiecewiseFunction'
    #     aboveContourDisplay.DataAxesGrid = 'GridAxesRepresentation'
    #     aboveContourDisplay.SelectionCellLabelFontFile = ''
    #     aboveContourDisplay.SelectionPointLabelFontFile = ''
    #     aboveContourDisplay.PolarAxes = 'PolarAxesRepresentation'
    #
    #     # init the 'GridAxesRepresentation' selected for 'DataAxesGrid'
    #     aboveContourDisplay.DataAxesGrid.XTitleFontFile = ''
    #     aboveContourDisplay.DataAxesGrid.YTitleFontFile = ''
    #     aboveContourDisplay.DataAxesGrid.ZTitleFontFile = ''
    #     aboveContourDisplay.DataAxesGrid.XLabelFontFile = ''
    #     aboveContourDisplay.DataAxesGrid.YLabelFontFile = ''
    #     aboveContourDisplay.DataAxesGrid.ZLabelFontFile = ''
    #
    #     # init the 'PolarAxesRepresentation' selected for 'PolarAxes'
    #     aboveContourDisplay.PolarAxes.Scale = [1.0, 1.0, 1000.0]
    #     aboveContourDisplay.PolarAxes.PolarAxisTitleFontFile = ''
    #     aboveContourDisplay.PolarAxes.PolarAxisLabelFontFile = ''
    #     aboveContourDisplay.PolarAxes.LastRadialAxisTextFontFile = ''
    #     aboveContourDisplay.PolarAxes.SecondaryRadialAxesTextFontFile = ''
    #
    #     # show data from dallasMap
    #     dallasMapDisplay = Show(dallasMap, renderView1)
    #
    #     # trace defaults for the display properties.
    #     dallasMapDisplay.Representation = 'Wireframe'
    #     dallasMapDisplay.AmbientColor = [0.5019607843137255, 0.5019607843137255,
    #                                      0.5019607843137255]
    #     dallasMapDisplay.ColorArrayName = ['POINTS', '']
    #     dallasMapDisplay.DiffuseColor = [0.7137254901960784, 0.7137254901960784,
    #                                      0.7137254901960784]
    #     dallasMapDisplay.MapScalars = 0
    #     dallasMapDisplay.InterpolateScalarsBeforeMapping = 0
    #     dallasMapDisplay.PointSize = 0.5
    #     dallasMapDisplay.LineWidth = 0.5
    #     dallasMapDisplay.Position = [0.0, 0.0, 305000.0]
    #     dallasMapDisplay.Scale = [1.0, 1.0, 1000.0]
    #     dallasMapDisplay.OSPRayScaleFunction = 'PiecewiseFunction'
    #     dallasMapDisplay.SelectOrientationVectors = 'None'
    #     dallasMapDisplay.ScaleFactor = 17341.236314833164
    #     dallasMapDisplay.SelectScaleArray = 'None'
    #     dallasMapDisplay.GlyphType = 'Arrow'
    #     dallasMapDisplay.GlyphTableIndexArray = 'None'
    #     dallasMapDisplay.GaussianRadius = 867.0618157416583
    #     dallasMapDisplay.SetScaleArray = [None, '']
    #     dallasMapDisplay.ScaleTransferFunction = 'PiecewiseFunction'
    #     dallasMapDisplay.OpacityArray = [None, '']
    #     dallasMapDisplay.OpacityTransferFunction = 'PiecewiseFunction'
    #     dallasMapDisplay.DataAxesGrid = 'GridAxesRepresentation'
    #     dallasMapDisplay.SelectionCellLabelFontFile = ''
    #     dallasMapDisplay.SelectionPointLabelFontFile = ''
    #     dallasMapDisplay.PolarAxes = 'PolarAxesRepresentation'
    #
    #     # init the 'GridAxesRepresentation' selected for 'DataAxesGrid'
    #     dallasMapDisplay.DataAxesGrid.XTitleFontFile = ''
    #     dallasMapDisplay.DataAxesGrid.YTitleFontFile = ''
    #     dallasMapDisplay.DataAxesGrid.ZTitleFontFile = ''
    #     dallasMapDisplay.DataAxesGrid.XLabelFontFile = ''
    #     dallasMapDisplay.DataAxesGrid.YLabelFontFile = ''
    #     dallasMapDisplay.DataAxesGrid.ZLabelFontFile = ''
    #
    #     # init the 'PolarAxesRepresentation' selected for 'PolarAxes'
    #     dallasMapDisplay.PolarAxes.Translation = [0.0, 0.0, 305000.0]
    #     dallasMapDisplay.PolarAxes.Scale = [1.0, 1.0, 1000.0]
    #     dallasMapDisplay.PolarAxes.PolarAxisTitleFontFile = ''
    #     dallasMapDisplay.PolarAxes.PolarAxisLabelFontFile = ''
    #     dallasMapDisplay.PolarAxes.LastRadialAxisTextFontFile = ''
    #     dallasMapDisplay.PolarAxes.SecondaryRadialAxesTextFontFile = ''
    #
    #     # setup the color legend parameters for each legend in this view
    #
    #     # get color legend/bar for densityLUT in view renderView1
    #     densityLUTColorBar = GetScalarBar(densityLUT, renderView1)
    #     densityLUTColorBar.WindowLocation = 'AnyLocation'
    #     densityLUTColorBar.Position = [0.031037827352085365, 0.6636363636363637]
    #     densityLUTColorBar.Title = 'density'
    #     densityLUTColorBar.ComponentTitle = ''
    #     densityLUTColorBar.TitleFontFile = ''
    #     densityLUTColorBar.LabelFontFile = ''
    #     densityLUTColorBar.ScalarBarLength = 0.3038636363636361
    #
    #     # set color bar visibility
    #     densityLUTColorBar.Visibility = 1
    #
    #     # show color legend
    #     densitiesDisplay.SetScalarBarVisibility(renderView1, True)
    #
    #     # show color legend
    #     belowContourDisplay.SetScalarBarVisibility(renderView1, True)
    #
    #     # show color legend
    #     aboveContourDisplay.SetScalarBarVisibility(renderView1, True)
    #
    #     # ----------------------------------------------------------------
    #     # setup color maps and opacity mapes used in the visualization
    #     # note: the Get..() functions create a new object, if needed
    #     # ----------------------------------------------------------------
    #
    #     # ----------------------------------------------------------------
    #     # finally, restore active source
    #     SetActiveSource(None)
    #     # ----------------------------------------------------------------
    #
    #     print("finished!")
    #
    #     if jpg:
    #         print("\tSaving .jpg file...", end=" ")
    #
    #         SaveScreenshot('STKDE_4D.jpg', ImageResolution=(1080, 720))
    #
    #         plt.subplots(figsize=(10.80, 7.2))
    #
    #         img = mpimg.imread('STKDE_4D.png')
    #         plt.imshow(img)
    #
    #         plt.axis('off')
    #         plt.show()
    #
    #         print("finished!")
    #     if interactive:
    #         print("\tInteractive Window Mode ON...", end=" ")
    #         Interact()
    #         print("finished!")

    def calculate_HR_PAI(self):
        PAIs = {}
        HRs = {}
        for i in range(1, 9):
            x, y, t = \
                np.array(self.predict_groups[f'group_{i}']['t2_data']['x']), \
                np.array(self.predict_groups[f'group_{i}']['t2_data']['y']), \
                np.array(self.predict_groups[f'group_{i}']['t2_data']['y_day'])

            if i == 1:
                x_training = pd.Series(
                    self.training_data["x"]).tolist() + pd.Series(
                    self.predict_groups[f'group_{i}']['t1_data']['x']).tolist()
                y_training = pd.Series(
                    self.training_data["y"]).tolist() + pd.Series(
                    self.predict_groups[f'group_{i}']['t1_data']['y']).tolist()
                t_training = pd.Series(
                    self.training_data["y_day"]).tolist() + pd.Series(
                    self.predict_groups[f'group_{i}']['t1_data'][
                        'y_day']).tolist()

            else:
                for j in range(1, i):
                    x_training += pd.Series(
                        self.predict_groups[f'group_{j}']['t2_data'][
                            'x']).tolist()
                    y_training += pd.Series(
                        self.predict_groups[f'group_{j}']['t2_data'][
                            'y']).tolist()
                    t_training += pd.Series(
                        self.predict_groups[f'group_{j}']['t2_data'][
                            'y_day']).tolist()

            self.kde = MyKDEMultivariate(
                [np.array(x_training),
                 np.array(y_training),
                 np.array(t_training)],
                'ccc')

            self.kde.resample(len(x_training))

            # self.train_model(
            #    x=np.array(self.training_data[['x']]),
            #   y=np.array(self.training_data[['y']]),
            #  t=np.array(self.training_data[['y_day']]))

            stkde = self.kde

            f_delitos = stkde.pdf([x, y, t])

            x, y, t = np.mgrid[
                      np.array(x_training).min():
                      np.array(x_training).max():100 * 1j,
                      np.array(y_training).min():
                      np.array(y_training).max():100 * 1j,
                      np.array(t_training).max():
                      np.array(t_training).max():1 * 1j
                      ]
            # print(t)
            # print(t.max())
            # print(t.min())

            f_nodos = stkde.pdf([x.flatten(), y.flatten(), t.flatten()])

            # c = np.linspace(0, max(f_delitos.max(), f_nodos.max()), 100)
            c = np.linspace(0, f_nodos.max(), 100)

            hits = [np.sum(f_delitos >= c[i]) for i in range(c.size)]

            area_h = [np.sum(f_nodos >= c[i]) for i in range(c.size)]

            HR = [i / len(f_delitos) for i in hits]
            area_percentaje = [i / len(f_nodos) for i in area_h]
            PAI = [float(HR[i]) / float(area_percentaje[i]) for i in
                   range(len(HR))]

            HRs[i] = [HR, area_percentaje, c]
            PAIs[i] = [PAI, area_percentaje]

        return {'HR': HRs, 'PAI': PAIs}

    def plot_HR(self):
        results = self.calculate_HR_PAI()['HR']
        plt.xlabel('Area percentage')
        plt.ylabel('HR')
        plt.title("HR vs Area")
        for i in range(1, 9):
            HRs, area_percentaje = results[i][0], results[i][1]
            plt.plot(area_percentaje, HRs, label=f'group {i}')
        plt.legend()
        plt.show()
        plt.savefig("HRvsArea")

    def plot_PAI(self):
        results = self.calculate_HR_PAI()['PAI']
        plt.xlabel('Area percentage')
        plt.ylabel('PAI')
        plt.title("PAI vs Area")
        for i in range(1, 9):
            PAIs, area_percentaje = results[i][0], results[i][1]
            plt.plot(area_percentaje, PAIs, label=f'group {i}')
        plt.legend()
        # plt.show()
        plt.savefig("PAIvsArea")

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

# if __name__ == "__main__":
#     st = time()
#
#     # %%
#
#     dallas_stkde = Framework(n=150000,
#                              year="2016")
#
#     print(dallas_stkde.training_data['x'])

# dallas_stkde.plot_HR()
# dallas_stkde.plot_PAI()

# # %%

# dallas_stkde.data_barplot(pdf=False)
# %%
# dallas_stkde.spatial_pattern(pdf=False)
# # %%
# dallas_stkde.contour_plot(bins=100,
#                           ti=183,
#                           pdf=False)
# # %%
# dallas_stkde.heatmap(bins=100,
#                      ti=365,
#                      pdf=False)
# # %%
# dallas_stkde.plot_4d(jpg=True,
#                      interactive=False)

# Testeando el resample() del predict_group 1...

# print(f"\nTotal time: {round((time() - st) / 60, 3)} min")

# %%

# s_points = dallas_stkde.predict_groups['group_1']['STKDE'] \
#     .resample(size=1000)

# %%

# def significance(data, stkde, sig):
#     all = []
#     for i, incident in data.iterrows():
#         p = stkde.pdf((incident["x"], incident["y"],
#                        incident["date"].timetuple().tm_yday))
#         all.append(p)
#     all.sort(reverse=True)
#     lim = int(len(all) * sig / 100)
#     max_s = all[: lim]
#     return max_s
#
#
# significance = 2
# test = dallas_stkde.predict_groups['group_1']['STKDE'].resample(size=1000)
# pdfs = []
# x, y, t = test
# for i, j, k in zip(x, y, t):
#     p = dallas_stkde.kde.pdf((i, j, k))
#     pdfs.append(p)
# max_pdfs = sorted(pdfs, reverse=True)[:int(len(pdfs) * significance /
#                                            100)]
# test = list(zip(x, y, t, max_pdfs))

# %%

# def calculate_hr(x, y, t, d, stkde):
#
#     c = max([i for i in d]) / 2
#     hot_spots = [i for i in all if i >= c]
#
#     return len(hot_spots) / len(d)

# %%

#
# stkde = dallas_stkde.predict_groups['group_1']['STKDE']
#
# #Simulación para modelo:
# for i in range(1, 9):
#     x, y, t = \
#         np.array(dallas_stkde.predict_groups[f'group_{i}']['t2_data']['x']), \
#         np.array(dallas_stkde.predict_groups[f'group_{i}']['t2_data']['y']), \
#         np.array(dallas_stkde.predict_groups[f'group_{i}']['t2_data']['y_day'])
#
#     f_delitos = stkde.pdf([x, y, t])
#
#     x, y, t = np.mgrid[
#               np.array(dallas_stkde.predict_groups[f"group_{i}"]["t1_data"]['x']).min():
#               np.array(dallas_stkde.predict_groups[f"group_{i}"]["t1_data"][['x']]).max():100 * 1j,
#               np.array(dallas_stkde.predict_groups[f"group_{i}"]["t1_data"][['y']]).min():
#               np.array(dallas_stkde.predict_groups[f"group_{i}"]["t1_data"][['y']]).max():100 * 1j,
#               np.array(dallas_stkde.predict_groups[f"group_{i}"]["t1_data"][
#                            ['y_day']]).min():
#               np.array(dallas_stkde.predict_groups[f"group_{i}"]["t1_data"][
#                            ['y_day']]).max():1 * 1j
#               ]
#
#
#
#     f_nodos = stkde.pdf([x.flatten(), y.flatten(), t.flatten()])
#
#     c = np.linspace(0, max(f_delitos.max(), f_nodos.max()), 100)
#
#     hits = [np.sum(f_delitos > c[i]) for i in range(c.size)]
#
#     area_h = [np.sum(f_nodos > c[i]) for i in range(c.size)]
#
#     HR = [i / len(f_delitos) for i in hits]
#     area_percentaje = [i / len(f_nodos) for i in area_h]
#     PAI = [float(HR[i]) / float(area_percentaje[i]) for i in range(len(HR) - 1)]

# plt.plot(c, HR)
# plt.xlabel('c')
# plt.ylabel('HR')
# plt.title("HR vs c")
# plt.show()
# plt.plot(c, HR, label=f'group {i}')
# plt.plot(c, area_percentaje, label=f'group {i}')
# plt.plot(area_percentaje, HR, label=f'group {i}')
# plt.show()
# plt.plot(area_percentaje[:-1], PAI, label=f'group {i}')
# plt.xlim([0, 0.2])
# plt.show()

# plt.legend()
# plt.savefig(f"all_groups.pdf", format='pdf')
