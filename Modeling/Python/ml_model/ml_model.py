"""
ml_model.py:
Python Version: 3.8.1

iPre - Big Data para Criminología
Created by Mauro S. Mendoza Elguera at 30-08-19
Pontifical Catholic University of Chile

Notes

-
"""

import pandas as pd
import numpy as np
import datetime
from calendar import month_name
from time import time

import geopandas as gpd

from shapely.geometry import Point
from fiona.crs import from_epsg

from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.svm import SVC

from sklearn.ensemble import AdaBoostClassifier, BaggingClassifier
from sklearn.metrics import precision_score, recall_score
from sklearn.metrics import confusion_matrix

from sodapy import Socrata
import credentials as cre

import seaborn as sbn
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from aux_functions import *
from parameters import *

pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', None)
pd.set_option('display.width', 1000)


class Framework:
    def __init__(self, n=1000, year="2017", read_df=False, read_data=False):
        self.n, self.year = n, year

        self.data = None  # Incidentes, geolocalización, dates, etc.
        self.df = None  # Nro. de incidentes por capas, etc.

        self.x, self.y = None, None
        self.nx, self.ny, self.hx, self.hy = None, None, None, None

        m_dict = {month_name[i]: None for i in range(1, 13)}
        self.incidents = {
            'Incidents': m_dict,
            'NC Incidents_1': m_dict,
            'NC Incidents_2': m_dict,
            'NC Incidents_3': m_dict,
            'NC Incidents_4': m_dict,
            'NC Incidents_5': m_dict,
            'NC Incidents_6': m_dict,
            'NC Incidents_7': m_dict,
        }

        if read_df:
            st = time()
            print("\nReading df pickle...", end=" ")
            self.df = pd.read_pickle('df.pkl')
            print(f"finished! ({time() - st:3.1f} sec)")
        if read_data:
            st = time()
            print("Reading data pickle...", end=" ")
            self.data = pd.read_pickle('data.pkl')
            print(f"finished! ({time() - st:3.1f} sec)")
        else:
            self.get_data()
            self.generate_df()

    @timer
    def get_data(self):
        """
        Obtención de datos a partir de la Socrata API.

        Por ahora se está realizando un filtro para obtener solo  incidentes
        asociados a robos residenciales

        :return:
        """

        print("\nRequesting data...")

        with Socrata(cre.socrata_domain,
                     cre.API_KEY_S,
                     username=cre.USERNAME_S,
                     password=cre.PASSWORD_S) as client:
            # Actualmente estamos filtrando por robos a domicilios
            where = \
                f"""
                    year1 = {self.year}
                    and date1 is not null
                    and time1 is not null
                    and x_coordinate is not null
                    and y_cordinate is not null
                    and offincident = 'BURGLARY OF HABITATION - FORCED ENTRY'
                """  #  571000 max. 09/07/2019

            results = client.get(cre.socrata_dataset_identifier,
                                 where=where,
                                 order="date1 ASC",
                                 limit=self.n,
                                 content_type='json')

            df = pd.DataFrame.from_records(results)

            print(f"\n\t{df.shape[0]} records successfully retrieved!")

            # DB Cleaning & Formatting
            df.loc[:, 'x_coordinate'] = df['x_coordinate'].apply(
                lambda x: float(x))
            df.loc[:, 'y_cordinate'] = df['y_cordinate'].apply(
                lambda x: float(x))
            df.loc[:, 'date1'] = df['date1'].apply(
                lambda x: datetime.datetime.strptime(
                    x.split('T')[0], '%Y-%m-%d')
            )
            df.loc[:, 'y_day'] = df["date1"].apply(
                lambda x: x.timetuple().tm_yday
            )

            df.rename(columns={'x_coordinate': 'x',
                               'y_cordinate': 'y',
                               'date1': 'date'},
                      inplace=True)
            df.sort_values(by=['date'], inplace=True)
            df.reset_index(drop=True, inplace=True)

            self.data = df

    @timer
    def generate_df(self):
        """
        La malla se genera de la esquina inf-izquierda a la esquina sup-derecha,
        partiendo con id = 0.

        n = i + j*n_x

        OBS.

        El numpy nd-array indexa de la esquina sup-izq a la inf-der
        [i:filas, j:columnas]

        El Pandas Dataframe comienza a indexar de la esquina inf-izq a la
        sup-der. [j, i]

        Hay que tener cuidado al momento de pasar desde la perspectiva
        matricial a la perspectiva normal de malla de ciudad, debido a las
        operaciones trasposición y luego up-down del nd-array entregan las
        posiciones reales para el pandas dataframe.

        :return: Pandas Dataframe con la información
        """

        print("\nGenerating dataframe...\n")

        # Creación de la malla
        print("\tCreating mgrid...")

        x_bins = abs(dallas_limits['x_max'] - dallas_limits['x_min']) / 100
        y_bins = abs(dallas_limits['y_max'] - dallas_limits['y_min']) / 100

        self.x, self.y = np.mgrid[
                         dallas_limits['x_min']:
                         dallas_limits['x_max']:x_bins * 1j,
                         dallas_limits['y_min']:
                         dallas_limits['y_max']:y_bins * 1j,
                         ]

        # Creación del esqueleto del dataframe
        print("\tCreating dataframe columns...")

        months = [month_name[i] for i in range(1, 13)]
        columns = pd.MultiIndex.from_product(
            [['Incidents_0', 'Incidents_1', 'Incidents_2', 'Incidents_3',
              'Incidents_4', 'Incidents_5', 'Incidents_6', 'Incidents_7'],
             months]
        )
        self.df = pd.DataFrame(columns=columns)

        # Creación de los parámetros para el cálculo de los índices
        print("\tFilling df...")

        self.nx = self.x.shape[0] - 1
        self.ny = self.y.shape[1] - 1
        self.hx = (self.x.max() - self.x.min()) / self.nx
        self.hy = (self.y.max() - self.y.min()) / self.ny

        # Manejo de los puntos de incidentes para poder trabajar en (x, y)
        geometry = [Point(xy) for xy in zip(
            np.array(self.data[['x']]),
            np.array(self.data[['y']]))
                    ]
        self.data = gpd.GeoDataFrame(self.data,  # gdf de incidentes
                                     crs=2276,
                                     geometry=geometry)
        self.data.to_crs(epsg=3857, inplace=True)
        self.data['Cell'] = None

        # Nro. incidentes en la i-ésima capa de la celda (i, j)
        for month in [month_name[i] for i in range(1, 13)]:
            print(f"\t\t{month}... ", end=' ')
            fil_incidents = self.data[self.data.month1 == month]
            D = np.zeros((self.nx, self.ny), dtype=int)

            for _, row in fil_incidents.iterrows():
                xi, yi = row.geometry.x, row.geometry.y
                nx_i = n_i(xi, self.x.min(), self.hx)
                ny_i = n_i(yi, self.y.min(), self.hy)
                D[nx_i, ny_i] += 1

            # Actualización del pandas dataframe
            self.df.loc[:, ('Incidents_0', month)] = to_df_col(D)
            self.df.loc[:, ('Incidents_1', month)] = \
                to_df_col(il_neighbors(matrix=D, i=1))
            self.df.loc[:, ('Incidents_2', month)] = \
                to_df_col(il_neighbors(matrix=D, i=2))
            self.df.loc[:, ('Incidents_3', month)] = \
                to_df_col(il_neighbors(matrix=D, i=3))
            self.df.loc[:, ('Incidents_4', month)] = \
                to_df_col(il_neighbors(matrix=D, i=4))
            self.df.loc[:, ('Incidents_5', month)] = \
                to_df_col(il_neighbors(matrix=D, i=5))
            self.df.loc[:, ('Incidents_6', month)] = \
                to_df_col(il_neighbors(matrix=D, i=6))
            self.df.loc[:, ('Incidents_7', month)] = \
                to_df_col(il_neighbors(matrix=D, i=7))

            print('finished!')

        # Adición de las columnas 'geometry' e 'in_dallas' al df
        print("\tPreparing df for filtering...")

        self.df['geometry'] = [Point(i) for i in
                               zip(self.x[:-1, :-1].flatten(),
                                   self.y[:-1, :-1].flatten())]
        self.df['in_dallas'] = 0

        # Filtrado de celdas (llenado de la columna 'in_dallas')
        self.df = filter_cells(self.df)
        self.df.drop(columns=[('in_dallas', '')], inplace=True)

        # Garbage recollection
        del self.incidents, self.x, self.y

    @timer
    def assign_cells(self, month='October'):
        """
        Asigna el número de celda asociado a cada incidente en self.data

        :return:
        """

        data = self.data[self.data.month1 == month]

        x_bins = abs(dallas_limits['x_max'] - dallas_limits['x_min']) / 100
        y_bins = abs(dallas_limits['y_max'] - dallas_limits['y_min']) / 100

        x, y = np.mgrid[
               dallas_limits['x_min']:
               dallas_limits['x_max']:x_bins * 1j,
               dallas_limits['y_min']:
               dallas_limits['y_max']:y_bins * 1j,
               ]

        nx = x.shape[0] - 1
        ny = y.shape[1] - 1
        hx = (x.max() - x.min()) / nx
        hy = (y.max() - y.min()) / ny

        for idx, inc in data.iterrows():
            xi, yi = inc.geometry.x, inc.geometry.y
            nx_i = n_i(xi, x.min(), hx)
            ny_i = n_i(yi, y.min(), hy)
            cell_idx = cell_index(nx_i, ny_i, ny)

            self.data.loc[idx, 'Cell'] = cell_idx

    @timer
    def ml_algorithm(self, f_importance=False, pickle=False):
        """
        Produce la predicción de acuerdo a los datos entregados, utilizando
        un approach de machine learning con clasificador RandomForest (rfc) y
        entrega el output asociado.

        :param f_importance: True para imprimir estadísticas
            asociadas a los features utilizados para entrenar el classifier
        :param pickle: True si se quiere generar un pickle de las estadísticas
            asociadas al entrenamiento del classifier
        """

        print("\nInitializing...")

        # Preparación del input para el algoritmo
        print("\n\tPreparing input...")

        # Jan-Sep
        x_ft = self.df.loc[
               :,
               [('Incidents_0', month_name[i]) for i in range(1, 10)] +
               [('Incidents_1', month_name[i]) for i in range(1, 10)] +
               [('Incidents_2', month_name[i]) for i in range(1, 10)] +
               [('Incidents_3', month_name[i]) for i in range(1, 10)] +
               [('Incidents_4', month_name[i]) for i in range(1, 10)] +
               [('Incidents_5', month_name[i]) for i in range(1, 10)] +
               [('Incidents_6', month_name[i]) for i in range(1, 10)] +
               [('Incidents_7', month_name[i]) for i in range(1, 10)]
               ]
        # Oct
        x_lbl = self.df.loc[
                :,
                [('Incidents_0', 'October'), ('Incidents_1', 'October'),
                 ('Incidents_2', 'October'), ('Incidents_3', 'October'),
                 ('Incidents_4', 'October'), ('Incidents_5', 'October'),
                 ('Incidents_6', 'October'), ('Incidents_7', 'October')]
                ]
        x_lbl[('Dangerous', '')] = x_lbl.T.any().astype(int)
        x_lbl = x_lbl[('Dangerous', '')]

        # Algoritmo
        print("\tRunning algorithms...")

        rfc = RandomForestClassifier(n_jobs=8)
        rfc.fit(x_ft, x_lbl.to_numpy().ravel())
        x_pred_rfc = rfc.predict(x_ft)

        if f_importance:
            cols = pd.Index(['features', 'r_importance'])
            rfc_fi_df = pd.DataFrame(columns=cols)
            rfc_fi_df['features'] = x_ft.columns.to_numpy()
            rfc_fi_df['r_importance'] = rfc.feature_importances_

            if pickle:
                rfc_fi_df.to_pickle('rfc.pkl')

        print("\n\tx\n")

        # Sirven para determinar celdas con TP/FN
        self.df[('Dangerous_Oct', '')] = x_lbl
        self.df[('Dangerous_pred_Oct', '')] = x_pred_rfc

        # Comparación para determinar si las celdas predichas son TP/FN
        self.df[('TP', '')] = 0
        self.df[('FN', '')] = 0
        self.df[('TP', '')] = np.where(
            (self.df[('Dangerous_Oct', '')] == self.df[
                ('Dangerous_pred_Oct', '')]) &
            (self.df[('Dangerous_Oct', '')] == 1),
            1,
            0
        )
        self.df[('FN', '')] = np.where(
            (self.df[('Dangerous_Oct', '')] != self.df[
                ('Dangerous_pred_Oct', '')]) &
            (self.df[('Dangerous_pred_Oct', '')] == 0),
            1,
            0
        )

        rfc_score = rfc.score(x_ft, x_lbl)
        rfc_precision = precision_score(x_lbl, x_pred_rfc)
        rfc_recall = recall_score(x_lbl, x_pred_rfc)
        print(
            f"""
    rfc score           {rfc_score:1.3f}
    rfc precision       {rfc_precision:1.3f}
    rfc recall          {rfc_recall:1.3f}
        """
        )

        print("\n\ty\n")

        y_ft = self.df.loc[
               :,
               [('Incidents_0', month_name[i]) for i in range(2, 11)] +
               [('Incidents_1', month_name[i]) for i in range(2, 11)] +
               [('Incidents_2', month_name[i]) for i in range(2, 11)] +
               [('Incidents_3', month_name[i]) for i in range(2, 11)] +
               [('Incidents_4', month_name[i]) for i in range(2, 11)] +
               [('Incidents_5', month_name[i]) for i in range(2, 11)] +
               [('Incidents_6', month_name[i]) for i in range(2, 11)] +
               [('Incidents_7', month_name[i]) for i in range(2, 11)]
               ]
        y_lbl = self.df.loc[
                :,
                [('Incidents_0', 'November'), ('Incidents_1', 'November'),
                 ('Incidents_2', 'November'), ('Incidents_3', 'November'),
                 ('Incidents_4', 'November'), ('Incidents_5', 'November'),
                 ('Incidents_6', 'November'), ('Incidents_7', 'November')]
                ]

        y_lbl[('Dangerous', '')] = y_lbl.T.any().astype(int)
        y_lbl = y_lbl[('Dangerous', '')]

        y_pred_rfc = rfc.predict(y_ft)

        rfc_score = rfc.score(y_ft, y_lbl.to_numpy().ravel())
        rfc_precision = precision_score(y_lbl, y_pred_rfc)
        rfc_recall = recall_score(y_lbl, y_pred_rfc)

        print(
            f"""
    rfc score           {rfc_score:1.3f}
    rfc precision       {rfc_precision:1.3f}
    rfc recall          {rfc_recall:1.3f}
            """
        )

        # Confusion Matrix

        # print("\tComputando matrices de confusión...", end="\n\n")
        #
        # c_matrix_x = confusion_matrix(
        #         x_lbl[('Dangerous', '')], x_predict[:, 0]
        # )
        #
        # print(c_matrix_x, end="\n\n")
        #
        # c_matrix_y = confusion_matrix(
        #         y_lbl[('Dangerous', '')], y_predict[:, 0]
        # )
        #
        # print(c_matrix_y)

    @timer
    def ml_algorithm_2(self, f_importance=False, pickle=False):
        """
        Algoritmo implementado con un Random Forest Regressor (rfr)

        :param f_importance:
        :param pickle:
        :return:
        """
        print("\nInitializing...")

        # Preparación del input para el algoritmo
        print("\n\tPreparing input...")

        # Jan-Sep
        x_ft = self.df.loc[
               :,
               [('Incidents_0', month_name[i]) for i in range(1, 10)] +
               [('Incidents_1', month_name[i]) for i in range(1, 10)] +
               [('Incidents_2', month_name[i]) for i in range(1, 10)] +
               [('Incidents_3', month_name[i]) for i in range(1, 10)] +
               [('Incidents_4', month_name[i]) for i in range(1, 10)] +
               [('Incidents_5', month_name[i]) for i in range(1, 10)] +
               [('Incidents_6', month_name[i]) for i in range(1, 10)] +
               [('Incidents_7', month_name[i]) for i in range(1, 10)]
               ]
        # Oct
        x_lbl = self.df.loc[
                :,
                [('Incidents_0', 'October'), ('Incidents_1', 'October'),
                 ('Incidents_2', 'October'), ('Incidents_3', 'October'),
                 ('Incidents_4', 'October'), ('Incidents_5', 'October'),
                 ('Incidents_6', 'October'), ('Incidents_7', 'October')]
                ]
        x_lbl[('Dangerous', '')] = x_lbl.T.any().astype(int)
        x_lbl = x_lbl[('Dangerous', '')]

        # Algoritmo
        print("\tRunning algorithms...")

        rfr = RandomForestRegressor(n_jobs=8)
        rfr.fit(x_ft, x_lbl.to_numpy().ravel())
        x_pred_rfc = rfr.predict(x_ft)

        # Sirven para determinar celdas con TP/FN
        self.df[('Dangerous_Oct_rfr', '')] = x_lbl
        self.df[('Dangerous_pred_Oct_rfr', '')] = x_pred_rfc

        # Estadísticas

        # rfr_score = rfr.score(x_ft, x_lbl)
        # rfr_precision = precision_score(x_lbl, x_pred_rfc)
        # rfr_recall = recall_score(x_lbl, x_pred_rfc)
        # print(
        #     f"""
        #     rfr score           {rfr_score:1.3f}
        #     rfr precision       {rfr_precision:1.3f}
        #     rfr recall          {rfr_recall:1.3f}
        #         """
        # )

        # Plot

        # Datos Oct luego de aplicar el rfr
        ans = self.df[[('geometry', ''), ('Dangerous_pred_Oct_rfr', '')]]
        ans = gpd.GeoDataFrame(ans)

        c = 0.50  # Threshold
        ans = ans[ans[('Dangerous_pred_Oct_rfr', '')] >= c]

        print("\tReading shapefile...")
        d_streets = gpd.GeoDataFrame.from_file(
            "../../Data/Streets/STREETS.shp")
        d_streets.to_crs(epsg=3857, inplace=True)

        print("\tRendering Plot...")
        fig, ax = plt.subplots(figsize=(20, 15))
        d_streets.plot(ax=ax,
                       alpha=0.4,
                       color="dimgrey",
                       label="Streets")

        ans.plot(ax=ax, column=('Dangerous_pred_Oct_rfr', ''), cmap='jet')

        # Background
        ax.set_axis_off()
        fig.set_facecolor('black')
        plt.show()
        plt.close()

    def calculate_hr(self, plot=False):
        """
        Calculates de Hit Rate for the given Framework

        :param plot: Plotea las celdas de los incidentes luego de aplicar
            un join
        :rtype: int
        :return:
        """

        incidents = pd.DataFrame(self.data)
        incidents_oct = incidents[incidents.month1 == 'October']  # 332

        data_oct = pd.DataFrame(self.data[fwork.data.month1 == 'October'])
        data_oct.drop(columns='geometry', inplace=True)

        ans = data_oct.join(other=self.df, on='Cell', how='left')
        ans = ans[ans[('geometry', '')].notna()]

        incidentsh = ans[ans[('Dangerous_pred_Oct', '')] == 1]
        incidentsh = ans[ans[('Dangerous_pred_Oct_rfr', '')] >= 0.9]

        hr = incidentsh.shape[0] / incidents_oct.shape[0]
        print(f"HR: {hr:1.3f}")

        return hr

    def calculate_pai(self):
        """
        Calcula el Predictive Accuracy Index (PAI)

        :return:
        """

        # data_oct = pd.DataFrame(self.data[self.data.month1 == 'October'])
        # data_oct.drop(columns='geometry', inplace=True)

        # ans = data_oct.join(other=fwork.df, on='Cell', how='left')
        # ans = self.df[self.df[('geometry', '')].notna()]

        # a = self.df[self.df[('Dangerous_pred_Oct', '')] == 1].shape[0]
        a = self.df[self.df[('Dangerous_pred_Oct_rfr', '')] >= 0.9].shape[0]
        A = self.df.shape[0]  # Celdas en Dallas

        hr = self.calculate_hr()
        ap = a / A

        print(f"a: {a} cells    A: {A} cells")
        print(f"Area Percentage: {ap:1.3f} %")
        print(f"PAI: {hr / ap:1.3f}")

        return hr / ap

    @timer
    def to_pickle(self, file_name):
        """
        Genera un pickle de self.df o self.data dependiendo el nombre
        dado (data.pkl o df.pkl)

        :param str file_name: Nombre del pickle a generar
        :return: pickle de self.df o self.data
        """

        print("\nPickling dataframe...", end=" ")
        if file_name == "df.pkl":
            self.df.to_pickle(file_name)
        if file_name == "data.pkl":
            if self.data is None:
                self.get_data()
                self.generate_df()
            self.data.to_pickle(file_name)

    @timer
    def plot_incidents(self, i_type="real", month="October"):
        """
        Plotea los incidentes almacenados en self.data en el mes dado.
        Asegurarse que al momento de realizar el ploteo, ya se haya
        hecho un llamado al método ml_algorithm() para identificar los
        incidentes TP y FN

        :param str i_type: Tipo de incidente a plotear (e.g. TP, FN, TP & FN)
        :param str month: String con el nombre del mes que se predijo
            con ml_algorithm()
        :return:
        """

        print(f"\nPlotting {month} Incidents...")
        print("\tFiltering incidents...")

        tp_data, fn_data, data = None, None, None

        if i_type == "TP & FN":
            data = gpd.GeoDataFrame(self.df)
            tp_data = data[self.df.TP == 1]
            fn_data = data[self.df.FN == 1]
        if i_type == "TP":
            data = gpd.GeoDataFrame(self.df)
            tp_data = self.df[self.df.TP == 1]
        if i_type == "FN":
            data = gpd.GeoDataFrame(self.df)
            fn_data = self.df[self.df.FN == 1]
        if i_type == "real":
            data = self.data[self.data.month1 == month]
            n_incidents = data.shape[0]
            print(f"\tNumber of Incidents in {month}: {n_incidents}")
        if i_type == "pred":
            data = gpd.GeoDataFrame(self.df)
            all_hp = data[self.df[('Dangerous_pred_Oct', '')] == 1]

        print("\tReading shapefile...")
        d_streets = gpd.GeoDataFrame.from_file(
            "../../Data/Streets/STREETS.shp")
        d_streets.to_crs(epsg=3857, inplace=True)

        print("\tRendering Plot...")
        fig, ax = plt.subplots(figsize=(20, 15))

        d_streets.plot(ax=ax,
                       alpha=0.4,
                       color="dimgrey",
                       zorder=2,
                       label="Streets")

        if i_type == 'pred':
            all_hp.plot(
                ax=ax,
                markersize=2.5,
                color='y',
                marker='o',
                zorder=3,
                label="TP Incidents"
            )
        if i_type == "real":
            data.plot(
                ax=ax,
                markersize=10,
                color='darkorange',
                marker='o',
                zorder=3,
                label="TP Incidents"
            )
        if i_type == "TP":
            tp_data.plot(
                ax=ax,
                markersize=2.5,
                color='red',
                marker='o',
                zorder=3,
                label="TP Incidents"
            )
        if i_type == "FN":
            fn_data.plot(
                ax=ax,
                markersize=2.5,
                color='blue',
                marker='o',
                zorder=3,
                label="FN Incidents"
            )
        if i_type == "TP & FN":
            tp_data.plot(
                ax=ax,
                markersize=2.5,
                color='red',
                marker='o',
                zorder=3,
                label="TP Incidents"
            )
            fn_data.plot(
                ax=ax,
                markersize=2.5,
                color='blue',
                marker='o',
                zorder=3,
                label="FN Incidents"
            )

        # Legends
        handles = [
            Line2D([], [],
                   marker='o',
                   color='darkorange',
                   label='Incident',
                   linestyle='None'),
            Line2D([], [],
                   marker='o',
                   color='red',
                   label='TP Incident',
                   linestyle='None'),
            Line2D([], [],
                   marker='o',
                   color="blue",
                   label="FN Incident",
                   linestyle='None'),
            Line2D([], [],
                   marker='o',
                   color='y',
                   label='Predicted Incidents',
                   linestyle='None')
        ]

        plt.legend(loc="best",
                   bbox_to_anchor=(0.1, 0.7),
                   frameon=False,
                   fontsize=13.5,
                   handles=handles)

        legends = ax.get_legend()
        for text in legends.get_texts():
            text.set_color('white')

        # Background
        ax.set_axis_off()
        fig.set_facecolor('black')
        plt.show()
        plt.close()

    @timer
    def plot_hotspots(self):
        """
        Utiliza el método estático asociado para plotear los hotspots
        con los datos ya cargados del framework.

        :return:
        """

        data = self.df[[('geometry', ''),
                        ('Dangerous_Oct', ''),
                        ('Dangerous_pred_Oct', '')]]

        # Quitamos el nivel ''
        data = data.T.reset_index(level=1, drop=True).T

        # Creamos el df para los datos reales (1) y predichos (2).
        data1 = data[['geometry', 'Dangerous_Oct']]
        data2 = data[['geometry', 'Dangerous_pred_Oct']]

        # Filtramos las celdas detectadas como Dangerous para reducir los
        # tiempos de cómputo.
        data1_d = data1[data1['Dangerous_Oct'] == 1]
        data1_nd = data1[data1['Dangerous_Oct'] == 0]
        geodata1_d = gpd.GeoDataFrame(data1_d)
        geodata1_nd = gpd.GeoDataFrame(data1_nd)

        data2_d = data2[data2['Dangerous_pred_Oct'] == 1]
        data2_nd = data2[data2['Dangerous_pred_Oct'] == 0]
        geodata2_d = gpd.GeoDataFrame(data2_d)
        geodata2_nd = gpd.GeoDataFrame(data2_nd)

        self.plot_hotspots_s(geodata1_d, geodata1_nd)
        self.plot_hotspots_s(geodata2_d, geodata2_nd)

    @staticmethod
    def plot_ft_imp_1():
        """
        Barplot de las importancias relativas de los datos agrupados
        por meses.

        :return:
        """

        df = pd.read_pickle('rfc.pkl')
        data = [aux_df.sum()['r_importance'] for aux_df in
                [df[df['features'].isin(
                    [(f'Incidents_{i}', month_name[j]) for i in range(0, 8)])]
                 for j in range(1, 10)]]
        index = [month_name[i] for i in range(1, 10)]

        ax = pd.DataFrame(data=data, index=index, columns=['r_importance']) \
            .plot.bar(y='r_importance', color='black', width=0.25, rot=0,
                      legend=None)

        for i in range(0, 9):
            plt.text(x=i - 0.3, y=data[i] + 0.02 * max(data),
                     s=f'{data[i]:.3f}')

        plt.xlabel("Features",
                   fontdict={'fontsize': 12.5,
                             'fontweight': 'bold',
                             'family': 'serif'},
                   labelpad=10
                   )
        plt.ylabel("Relative Importance",
                   fontdict={'fontsize': 12.5,
                             'fontweight': 'bold',
                             'family': 'serif'},
                   labelpad=7.5
                   )
        plt.xticks(ticks=[i for i in range(0, 9)],
                   labels=[f'{month_name[i]:.3s}' for i in range(1, 10)])
        plt.tick_params(axis='both', length=0, pad=8.5)

        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)

        ax.spines['bottom'].set_color('lightgray')
        ax.spines['left'].set_color('lightgray')

        plt.show()

    @staticmethod
    def plot_ft_imp_2():
        """
        Barplot de las importancias relativas de los datos agrupados en
        capas.

        :return:
        """

        df = pd.read_pickle('rfc.pkl')
        data = [aux_df.sum()['r_importance'] for aux_df in
                [df[df['features'].isin(
                    [(f'Incidents_{i}', month_name[j]) for j in range(1, 10)])]
                 for i in range(0, 8)]]
        index = [i for i in range(0, 8)]

        ax = pd.DataFrame(data=data, index=index, columns=['r_importance']) \
            .plot.bar(y='r_importance', color='black', width=0.25, rot=0,
                      legend=None)

        for i in range(0, 8):
            plt.text(x=i - 0.3, y=data[i] + 0.02 * max(data),
                     s=f'{data[i]:.3f}')

        plt.xlabel("Layers",
                   fontdict={'fontsize': 12.5,
                             'fontweight': 'bold',
                             'family': 'serif'},
                   labelpad=10
                   )
        plt.ylabel("Relative Importance",
                   fontdict={'fontsize': 12.5,
                             'fontweight': 'bold',
                             'family': 'serif'},
                   labelpad=7.5
                   )
        plt.xticks(ticks=[i for i in range(0, 8)],
                   labels=[f'{i}' for i in range(0, 8)])
        plt.tick_params(axis='both', length=0, pad=8.5)  # Hide tick lines

        ax.spines['top'].set_visible(False)  # Hide frame
        ax.spines['right'].set_visible(False)

        ax.spines['bottom'].set_color('lightgray')  # Frame color
        ax.spines['left'].set_color('lightgray')

        plt.show()

    @staticmethod
    def plot_ft_imp_3():
        """
        Heatmap que combina las estadísticas de las importancias
        relativas de los datos agrupados por meses y capas.

        :return:
        """

        df = pd.read_pickle('rfc.pkl')
        df.set_index(keys='features', drop=True, inplace=True)

        data = df.to_numpy().reshape(8, 9).T
        columns = [i for i in range(0, 8)]
        index = [f'{month_name[i]:.3s}' for i in range(1, 10)]

        df = pd.DataFrame(data=data, index=index, columns=columns)

        sbn.heatmap(data=df, annot=True, annot_kws={"fontsize": 9})

        plt.xlabel("Layers",
                   fontdict={'fontsize': 12.5,
                             'fontweight': 'bold',
                             'family': 'serif'},
                   labelpad=10
                   )
        plt.ylabel("Months",
                   fontdict={'fontsize': 12.5,
                             'fontweight': 'bold',
                             'family': 'serif'},
                   labelpad=7.5
                   )

        plt.tick_params(axis='both', length=0, pad=8.5)
        plt.yticks(rotation=0)

        plt.show()

    @staticmethod
    def plot_hotspots_s(geodata_d, geodata_nd):
        """
            Plotea las celdas de Dallas reconocidas como peligrosas o
            no-peligrosas de acuerdo al algoritmo.

            :param gpd.GeoDataFrame geodata_d: gdf con los puntos de celdas
                peligrosas
            :param gpd.GeoDataFrame geodata_nd: gdf con los puntos de celdas
                no-peligrosas
            """

        print('Reading shapefiles...')
        d_streets = gpd.GeoDataFrame.from_file(
            filename='../../Data/Streets/STREETS.shp'
        )
        d_districts = gpd.GeoDataFrame.from_file(
            filename='../../Data/Councils/Councils.shp'
        )
        d_streets.to_crs(epsg=3857, inplace=True)
        d_districts.to_crs(epsg=3857, inplace=True)

        fig, ax = plt.subplots(figsize=(20, 15))
        ax.set_facecolor('xkcd:black')

        for district, data in d_districts.groupby('DISTRICT'):
            data.plot(ax=ax,
                      color=d_colors[district],
                      linewidth=2.5,
                      edgecolor="black")

        handles = [Line2D([], [], marker='o', color='red',
                          label='Dangerous Cell',
                          linestyle='None'),
                   Line2D([], [], marker='o', color="blue",
                          label="Non-Dangerous Cell",
                          linestyle='None')]

        d_streets.plot(ax=ax,
                       alpha=0.4,
                       color="dimgrey",
                       zorder=2,
                       label="Streets")
        geodata_nd.plot(ax=ax,
                        markersize=10,
                        color='blue',
                        marker='o',
                        zorder=3,
                        label="Incidents")
        geodata_d.plot(ax=ax,
                       markersize=10,
                       color='red',
                       marker='o',
                       zorder=3,
                       label="Incidents")

        plt.legend(loc="best",
                   bbox_to_anchor=(0.1, 0.7),
                   frameon=False,
                   fontsize=13.5,
                   handles=handles)

        legends = ax.get_legend()
        for text in legends.get_texts():
            text.set_color('white')

        ax.set_axis_off()
        fig.set_facecolor('black')
        plt.show()
        plt.close()

    @timer
    def plot_joined_cells(self):
        """

        :return:
        """

        data_oct = pd.DataFrame(self.data[self.data.month1 == 'October'])
        data_oct.drop(columns='geometry', inplace=True)

        ans = data_oct.join(other=fwork.df, on='Cell', how='left')
        ans = ans[ans[('geometry', '')].notna()]

        gpd_ans = gpd.GeoDataFrame(ans, geometry=ans[('geometry', '')])

        d_streets = gpd.GeoDataFrame.from_file(
            "../../Data/Streets/STREETS.shp")
        d_streets.to_crs(epsg=3857, inplace=True)

        fig, ax = plt.subplots(figsize=(20, 15))

        d_streets.plot(ax=ax,
                       alpha=0.4,
                       color="dimgrey",
                       zorder=2,
                       label="Streets")

        gpd_ans.plot(
            ax=ax,
            markersize=10,
            color='red',
            marker='o',
            zorder=3,
            label="Joined Incidents"
        )

        handles = [
            Line2D([], [],
                   marker='o',
                   color='red',
                   label='Joined Incidents',
                   linestyle='None'),
        ]

        plt.legend(loc="best",
                   bbox_to_anchor=(0.1, 0.7),
                   frameon=False,
                   fontsize=13.5,
                   handles=handles)

        legends = ax.get_legend()
        for text in legends.get_texts():
            text.set_color('white')

        # Background
        ax.set_axis_off()
        fig.set_facecolor('black')
        plt.show()
        plt.close()


if __name__ == "__main__":
    # TODO (Personal)
    #   - Eliminar el FutureWarning del .to_crs()                   PENDIENTE

    # TODO (Reu. 05/03)
    #   - TP/FN en el plot de delitos de octubre                    √
    #   - HR calculado en base a los delitos y no a las celdas      √
    #       (por eso no se usará el recall como HR)
    #   - Buscar clf que trabaje con un valor real entre (0, 1)     PENDIENTE
    #       * Ahí se debe obtener el plot a colores de dallas
    #       * PAI / a/A   ,   HR / a/A

    # TODO (Reu. 13/03)
    #   - Pensar la forma de relacionar los incidentes con las celdas
    #       asociadas (id), para poder calcular el HR [Ponderar TP cells
    #       con el número de incidentes en ellas.                   √
    #   - RECUERDA REALIZAR LA COMPARACIÓN CON Dangerous_pred_Oct
    #       y no TP/FN                                              √

    # TODO (Reu. 30/03)
    #   - Calcular Area percentage Dangerous cells / all            √
    #   - skelearn para valores reales en Dangerous label           √
    #   - Plotear heatmap con valores reales                        √
    #   - Proceso similar al STKDE, luego del clf con labels reales PENDIENTE
    #       luego implementar parámetro "c", que modifica HR y A.p.

    # TODO (Reu. 13/04)
    #   - c vs HR, c vs Area Percetage , (c vs PAI), a/A vs PAI, a/A vs HR (rfr)
    #   - (a/A, API), (a/A, HR) añadir en los plots del clf

    fwork = Framework(n=150000, year="2017", read_df=True, read_data=True)
    # fwork.ml_algorithm()
    # fwork.assign_cells()
    fwork.calculate_pai()
    # fwork.ml_algorithm_2()

    # aux_df = fwork.df
    #
    # X1 = aux_df.loc[:,
    #       [('Incidents', month_name[i]) for i in range(1, 10)] +
    #       [('NC Incidents', month_name[i]) for i in range(1, 10)]
    #      ].to_numpy()
    #
    # y1 = aux_df.loc[:,
    #       [('Incidents', 'October'), ('NC Incidents', 'October')]
    #      ]
    #
    # y1[('Dangerous', '')] = ((y1[('Incidents', 'October')] != 0) |
    #                         (y1[('NC Incidents', 'October')] != 0)) \
    #     .astype(int)
    # y1.drop([('Incidents', 'October'), ('NC Incidents', 'October')],
    #         axis=1,
    #         inplace=True)
    #
    # y1 = y1.to_numpy().ravel()

    # bc = BaggingClassifier(RandomForestClassifier(n_jobs=8), n_jobs=8)
    # bc.fit(X1, y1)

    # print(
    #     f"""
    # bc score           {bc.score(X1, y1):1.3f}
    # bc precision       {precision_score(y1, bc.predict(X1)):1.3f}
    # bc recall          {recall_score(y1, bc.predict(X1)):1.3f}
    #         """
    # )
    #
    # abc = AdaBoostClassifier()
    # abc.fit(X1, y1)
    #
    # print(
    #     f"""
    # abc score           {abc.score(X1, y1):1.3f}
    # abc precision       {precision_score(y1, abc.predict(X1)):1.3f}
    # abc recall          {recall_score(y1, abc.predict(X1)):1.3f}
    #     """
    # )

    # X2 = aux_df.loc[:,
    #        [('Incidents', month_name[i]) for i in range(2, 11)] +
    #        [('NC Incidents', month_name[i]) for i in range(2, 11)]
    #      ].to_numpy()
    #
    # y2 = aux_df.loc[:,
    #         [('Incidents', 'November')] + [('NC Incidents', 'November')]]
    # y2[('Dangerous', '')] = \
    #     ((y2[('Incidents', 'November')] != 0) |
    #      (y2[('NC Incidents', 'November')] != 0)).astype(int)
    # y2.drop([('Incidents', 'November'), ('NC Incidents', 'November')],
    #         axis=1, inplace=True)
    #
    # y2 = y2.to_numpy().ravel()
