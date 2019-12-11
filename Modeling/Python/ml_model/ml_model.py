# ml_model.py:
# - Python version: 3.7.1
# - Author: Mauro S. Mendoza Elguera
# - Institution: Pontifical Catholic University of Chile
# - Date: 2019-08-30

import pandas as pd
import numpy as np
import datetime
from calendar import month_name
from time import time

import pickle

import geopandas as gpd
from shapely.geometry import Point
from fiona.crs import from_epsg

from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC

from sklearn.ensemble import AdaBoostClassifier, BaggingClassifier

from sklearn.metrics import precision_score, recall_score
# from sklearn.metrics import confusion_matrix

from sodapy import Socrata
import credentials as cre

from aux_functions import n_i, nc_incidents, to_df_col, filter_cells
from parameters import dallas_limits

pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', None)
pd.set_option('display.width', 1000)


def timer(fn):
    def inner_1(*args, **kwargs):
        st = time()

        fn(*args, **kwargs)

        print(f"\n\tFinished! ({time() - st:3.1f} sec)")

    return inner_1


# noinspection DuplicatedCode
class Framework:
    def __init__(self, n=1000, year="2017", read_df=True):
        self.n, self.year = n, year

        self.df = None

        self.x, self.y = None, None
        self.nx, self.ny, self.hx, self.hy = None, None, None, None

        m_dict = {month_name[i]: None for i in range(1, 13)}
        self.incidents = {
            'Incidents': m_dict,
            'NC Incidents': m_dict
        }

        if read_df:
            st = time()

            print("\nReading pickle dataframe...", end=" ")
            self.df = pd.read_pickle('df_pickle')
            print(f"finished! ({time() - st:3.1f} sec)")
        else:
            self.data = self.get_data()
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

            return df

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

        st = time()

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
        cols = pd.MultiIndex.from_product(
            [['Incidents', 'NC Incidents'], months]
        )

        self.df = pd.DataFrame(columns=cols)

        # Creación de los parámetros para el cálculo de los índices

        print("\tFilling df...")

        self.nx = self.x.shape[0] - 1
        self.ny = self.y.shape[1] - 1

        self.hx = (self.x.max() - self.x.min()) / self.nx
        self.hy = (self.y.max() - self.y.min()) / self.ny

        # Manejo de los puntos de incidentes para poder trabajar en (x, y)
        # y realizar Feature Engineering

        geometry = [Point(xy) for xy in zip(
            np.array(self.data[['x']]),
            np.array(self.data[['y']]))
                    ]
        self.data = gpd.GeoDataFrame(self.data,  # gdf de incidentes
                                     crs=2276,
                                     geometry=geometry)

        self.data.to_crs(epsg=3857, inplace=True)

        # Nro. incidentes en la celda(i, j) + Nro. incidentes en celdas vecinas

        for month in [month_name[i] for i in range(1, 13)]:
            fil_incidents = self.data[self.data.month1 == month]

            D = np.zeros((self.nx, self.ny), dtype=int)

            for index, row in fil_incidents.iterrows():
                xi, yi = row.geometry.x, row.geometry.y

                nx_i = n_i(xi, self.x.min(), self.hx)
                ny_i = n_i(yi, self.y.min(), self.hy)

                D[nx_i, ny_i] += 1

            # Actualización del diccionario con las matrices

            self.incidents['Incidents'][month] = D
            self.incidents['NC Incidents'][month] = nc_incidents(D)

            # Actualización del pandas dataframe

            self.df.loc[:, ('Incidents', month)] = to_df_col(D)
            self.df.loc[:, ('NC Incidents', month)] = to_df_col(
                nc_incidents(D))

        # Adición de las columnas 'geometry' e 'in_dallas' al df

        print("\tPreparing df for filtering...")

        self.df['geometry'] = [Point(i) for i in
                               zip(self.x[:-1, :-1].flatten(),
                                   self.y[:-1, :-1].flatten())]
        self.df['in_dallas'] = 0

        # Llenado de la columna 'in_dallas'

        self.df = filter_cells(self.df)

        # Binary Classification

        # self.df[('Risky', '')] = 0

        # Multinominal Classification

        # self.df[('Risky', '')] = 0

        # Garbage recollection

        del self.data, self.incidents, self.x, self.y

        print(f"{time() - st:3.2f} sec")

    @staticmethod
    @timer
    def ml_p_algorithm():
        """
        Produce la predicción de acuerdo a los datos entregados, utilizando
        un approach de machine learning con clasificador RandomForest y
        entrega el output asociado a la matriz de confusión.

        :return:
        """

        print("\nInitializing...")

        # Preparación del input para el algoritmo

        print("\n\tPreparing input...")

        aux_df = fwork.df

        x_ft = aux_df.loc[:,
               [('Incidents', month_name[i]) for i in range(1, 10)] +
               [('NC Incidents', month_name[i]) for i in range(1, 10)]
               ]

        x_lbl = aux_df.loc[:,
                [('Incidents', 'October'), ('NC Incidents', 'October')]
                ]
        x_lbl[('Insecure', '')] = ((x_lbl[('Incidents', 'October')] != 0) |
                                   (x_lbl[('NC Incidents', 'October')] != 0)) \
            .astype(int)
        x_lbl.drop([('Incidents', 'October'), ('NC Incidents', 'October')],
                   axis=1,
                   inplace=True)

        x_ft = x_ft.to_numpy()
        x_lbl = x_lbl.to_numpy().ravel()

        # Algoritmo

        print("\tRunning algorithms...")

        rfc = RandomForestClassifier(n_jobs=8)
        dtc = DecisionTreeClassifier()
        rbf_svm = SVC()

        rfc.fit(x_ft, x_lbl)
        dtc.fit(x_ft, x_lbl)
        rbf_svm.fit(x_ft, x_lbl)

        x_pred_rfc = rfc.predict(x_ft)
        x_pred_dtc = dtc.predict(x_ft)
        x_pred_rbf_svm = rbf_svm.predict(x_ft)

        print("\n\tx\n")

        rfc_score = rfc.score(x_ft, x_lbl)
        dtc_score = dtc.score(x_ft, x_lbl)
        rbf_svm_score = rbf_svm.score(x_ft, x_lbl)

        rfc_precision = precision_score(x_lbl, x_pred_rfc)
        dtc_precision = precision_score(x_lbl, x_pred_dtc)
        rbf_svm_precision = precision_score(x_lbl, x_pred_rbf_svm)

        rfc_recall = recall_score(x_lbl, x_pred_rfc)
        dtc_recall = recall_score(x_lbl, x_pred_dtc)
        rbf_svm_recall = recall_score(x_lbl, x_pred_rbf_svm)

        print(
            f"""
    rfc score           {rfc_score:1.3f}
    rfc precision       {rfc_precision:1.3f}
    rfc recall          {rfc_recall:1.3f}

    dtc score           {dtc_score:1.3f}
    dtc precision       {dtc_precision:1.3f}
    dtc recall          {dtc_recall:1.3f}

    rbf_svm score       {rbf_svm_score:1.3f}
    rbf_svm precision   {rbf_svm_precision:1.3f}
    rbf_svm recall      {rbf_svm_recall:1.3f}
            """
        )

        print("\n\ty\n")

        y_ft = aux_df.loc[:,
               [('Incidents', month_name[i]) for i in range(2, 11)] +
               [('NC Incidents', month_name[i]) for i in range(2, 11)]
               ]

        y_lbl = aux_df.loc[:,
                [('Incidents', 'November')] + [('NC Incidents', 'November')]]
        y_lbl[('Insecure', '')] = \
            ((y_lbl[('Incidents', 'November')] != 0) |
             (y_lbl[('NC Incidents', 'November')] != 0)).astype(int)
        y_lbl.drop([('Incidents', 'November'), ('NC Incidents', 'November')],
                   axis=1, inplace=True)

        y_pred_rfc = rfc.predict(y_ft)
        y_pred_dtc = dtc.predict(y_ft)
        y_pred_rbf_svm = rbf_svm.predict(y_ft)

        rfc_score = rfc.score(y_ft, y_lbl)
        dtc_score = dtc.score(y_ft, y_lbl)
        rbf_svm_score = rbf_svm.score(y_ft, y_lbl)

        rfc_precision = precision_score(y_lbl, y_pred_rfc)
        dtc_precision = precision_score(y_lbl, y_pred_dtc)
        rbf_svm_precision = precision_score(y_lbl, y_pred_rbf_svm)

        rfc_recall = recall_score(y_lbl, y_pred_rfc)
        dtc_recall = recall_score(y_lbl, y_pred_dtc)
        rbf_svm_recall = recall_score(y_lbl, y_pred_rbf_svm)

        print(
            f"""
    rfc score           {rfc_score:1.3f}
    rfc precision       {rfc_precision:1.3f}
    rfc recall          {rfc_recall:1.3f}

    dtc score           {dtc_score:1.3f}
    dtc precision       {dtc_precision:1.3f}
    dtc recall          {dtc_recall:1.3f}

    rbf_svm score       {rbf_svm_score:1.3f}
    rbf_svm precision   {rbf_svm_precision:1.3f}
    rbf_svm recall      {rbf_svm_recall:1.3f}
            """
        )

        # Confusion Matrix

        # print("\tComputando matrices de confusión...", end="\n\n")
        #
        # c_matrix_x = confusion_matrix(
        #         x_lbl[('Insecure', '')], x_predict[:, 0]
        # )
        #
        # print(c_matrix_x, end="\n\n")
        #
        # c_matrix_y = confusion_matrix(
        #         y_lbl[('Insecure', '')], y_predict[:, 0]
        # )
        #
        # print(c_matrix_y)


if __name__ == "__main__":
    # TODO
    #       - Aumentar las capas de NC_incidents
    #       - Comparar entre etiqueta 1 y 3
    #       - Mejorar feature engineering con medidas del modelo ProMap
    #       - Pensar implementación de HR/PAI
    #       - Comparación de rendimiento Bin. Class vs Multi. Class
    #       - Comparar 0s entre xy_predicted

    fwork = Framework(n=150000, year="2017", read_df=True)

    aux_df = fwork.df

    X_1 = aux_df.loc[:,
          [('Incidents', month_name[i]) for i in range(1, 10)] +
          [('NC Incidents', month_name[i]) for i in range(1, 10)]
          ].to_numpy()

    y_1 = aux_df.loc[:,
          [('Incidents', 'October'), ('NC Incidents', 'October')]
          ]

    y_1[('Insecure', '')] = ((y_1[('Incidents', 'October')] != 0) |
                             (y_1[('NC Incidents', 'October')] != 0)) \
        .astype(int)
    y_1.drop([('Incidents', 'October'), ('NC Incidents', 'October')],
             axis=1,
             inplace=True)

    y_1 = y_1.to_numpy()

    bc = BaggingClassifier(RandomForestClassifier(n_jobs=8), n_jobs=8)
    bc.fit(X_1, y_1)

    print(
        f"""
    bc score           {bc.score(X_1, y_1):1.3f}
    bc precision       {precision_score(y_1, bc.predict(X_1)):1.3f}
    bc recall          {recall_score(y_1, bc.predict(X_1)):1.3f}
            """
    )

    abc = AdaBoostClassifier()
    abc.fit(X_1, y_1)

    print(
        f"""
    abc score           {abc.score(X_1, y_1):1.3f}
    abc precision       {precision_score(y_1, abc.predict(X_1)):1.3f}
    abc recall          {recall_score(y_1, abc.predict(X_1)):1.3f}
        """
    )