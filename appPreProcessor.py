# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# File Author: Matthieu Berthomé                           #
# Date: 5/26/2017                                          #
# MIT Licence                                              #
# ##########################################################

# ########################################################## ##
# FlatCAM 9 Neo S2                                            #
# Shapely 2.x Friendly Edition                                #
# Community modernized fork                                   #
# Maintained by Luis Enrique Yacupoma Aguirre                 #
# Date: 01/06/2026                                            #
# https://github.com/ProgLuis/FlatCAM9NeoS2                   #
# ########################################################## ##

from importlib.machinery import SourceFileLoader
import os
from abc import ABCMeta, abstractmethod
import math

# module-root dictionary of preprocessors

import logging

log = logging.getLogger('base')
preprocessors = {}


class ABCPreProcRegister(ABCMeta):
    # handles preprocessors registration on instantiation
    def __new__(cls, clsname, bases, attrs):
        newclass = super(ABCPreProcRegister, cls).__new__(cls, clsname, bases, attrs)
        if object not in bases:
            name = newclass.__name__
            if name not in preprocessors:
                preprocessors[name] = newclass()  # registrar solo si es nuevo
            # Si ya existe, no hacemos nada (sin warning, sin sobrescritura)               
            else:
                pass
                ## Si ya existe, no registramos pero mandamos mensaje
                ## Este codigo fue editado para evitar el ruido en consola al inicio de flatcam
                ## log.debug(f'Preprocessor {name} already loaded, skipping duplicate')                        
                
        return newclass


class PreProc(object, metaclass=ABCPreProcRegister):
    @abstractmethod
    def start_code(self, p):
        pass

    @abstractmethod
    def lift_code(self, p):
        pass

    @abstractmethod
    def down_code(self, p):
        pass

    @abstractmethod
    def toolchange_code(self, p):
        pass

    @abstractmethod
    def up_to_zero_code(self, p):
        pass

    @abstractmethod
    def rapid_code(self, p):
        pass

    @abstractmethod
    def linear_code(self, p):
        pass

    @abstractmethod
    def end_code(self, p):
        pass

    @abstractmethod
    def feedrate_code(self, p):
        pass

    @abstractmethod
    def spindle_code(self, p):
        pass

    @abstractmethod
    def spindle_stop_code(self, p):
        pass


class AppPreProcTools(object, metaclass=ABCPreProcRegister):
    @abstractmethod
    def start_code(self, p):
        pass

    @abstractmethod
    def lift_code(self, p):
        pass

    @abstractmethod
    def down_z_start_code(self, p):
        pass

    @abstractmethod
    def lift_z_dispense_code(self, p):
        pass

    @abstractmethod
    def down_z_stop_code(self, p):
        pass

    @abstractmethod
    def toolchange_code(self, p):
        pass

    @abstractmethod
    def rapid_code(self, p):
        pass

    @abstractmethod
    def linear_code(self, p):
        pass

    @abstractmethod
    def end_code(self, p):
        pass

    @abstractmethod
    def feedrate_xy_code(self, p):
        pass

    @abstractmethod
    def z_feedrate_code(self, p):
        pass

    @abstractmethod
    def feedrate_z_dispense_code(self, p):
        pass

    @abstractmethod
    def spindle_fwd_code(self, p):
        pass

    @abstractmethod
    def spindle_rev_code(self, p):
        pass

    @abstractmethod
    def spindle_off_code(self, p):
        pass

    @abstractmethod
    def dwell_fwd_code(self, p):
        pass

    @abstractmethod
    def dwell_rev_code(self, p):
        pass


def load_preprocessors(app):
    preprocessors_path_search = [
        os.path.join(app.data_path, 'preprocessors', '*.py'),
        os.path.join('preprocessors', '*.py')
    ]
    import glob
    for path_search in preprocessors_path_search:
        for file in glob.glob(path_search):
            try:
                SourceFileLoader('FlatCAMPostProcessor', file).load_module()
            except Exception as e:
                app.log.error(str(e))
    return preprocessors
