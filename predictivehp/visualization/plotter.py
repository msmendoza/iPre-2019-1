"""
plotter.py
Python Version: 3.8.1

iPre - Big Data para Criminología
Created by Mauro S. Mendoza Elguera at 20-05-20
Pontifical Catholic University of Chile

"""


class Plotter:
    def __init__(self, models=None):
        """

        :param list models: Lista con los objetos de los diferentes
            modelos. e.g. [stkde, rfr, pm]
        """
        self.models = [] if not models else models

    def add_model(self, model):
        self.models.append(model)

    def heatmap(self):
        pass

    def hr(self):
        pass

    def pai(self):
        pass


if __name__ == '__main__':
    pass
