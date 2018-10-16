#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#    Project: Fast Azimuthal integration
#             https://github.com/silx-kit/pyFAI
#
#
#    Copyright (C) 2013-2018 European Synchrotron Radiation Facility, Grenoble, France
#
#    Authors: Jérôme Kieffer <Jerome.Kieffer@ESRF.eu>
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#  .
#  The above copyright notice and this permission notice shall be included in
#  all copies or substantial portions of the Software.
#  .
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#  THE SOFTWARE.

"""utility that averages out a serie of files"""

__author__ = "Jerome Kieffer, Picca Frédéric-Emmanuel"
__contact__ = "Jerome.Kieffer@ESRF.eu"
__license__ = "MIT"
__copyright__ = "European Synchrotron Radiation Facility, Grenoble, France"
__date__ = "09/10/2018"
__status__ = "production"

import os
import logging
from argparse import ArgumentParser

logger = logging.getLogger("average")
logging.basicConfig(level=logging.INFO)
logging.captureWarnings(True)

import pyFAI.utils.shell
import pyFAI.utils.stringutil
from pyFAI import average


class PreEmitStreamHandler(logging.Handler):
    """Handler allowing to hook a function before the emit function.

    The main logging feature is delegated to a sub handler.
    """

    def __init__(self, handler):
        self._handler = handler

    def emit(self, record):
        """
        Call pre_emit function then delegate the emit to the sub handler.

        :type record: logging.LogRecord
        """
        self.pre_emit()
        self._handler.emit(record)

    def __getattr__(self, attr):
        """Reach the attribute from the sub handler and cache it to the current
        object"""
        value = getattr(self._handler, attr)
        setattr(self, attr, value)
        return value

    def pre_emit(self):
        pass


def patch_logging_handler(callback):
    """Patch the logging system to have a working progress bar with out glitch.
    pyFAI define a default handler then we have to rework it"""
    # remove the default logging handler
    # it can come from pyFAI.__init__
    root_logger = logging.getLogger()
    # assume there is a logger
    assert(len(root_logger.handlers) == 1)
    root_handler = root_logger.handlers[0]
    root_logger.removeHandler(root_handler)
    # use our custom handler
    handler = PreEmitStreamHandler(root_handler)
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    handler.pre_emit = callback


def parse_algorithms(options):
    """Return a list of initilized algorithms from the command line"""

    methods = options.method.split(",")
    methods = set(methods) - set([""])

    if options.quantiles:
        for sep in "-,":
            if sep in options.quantiles:
                q1, q2 = options.quantiles.split(sep, 1)
                break
        else:
            q1 = float(options.quantiles)
            q2 = 1.0 - q1
        quantiles = (float(q1), float(q2))

        if "quanties" not in methods:
            logger.warning("Add quantiles to the set of methods as quantiles parameters is defined.")
            methods.add("quantiles")
    else:
        quantiles = None
        if "quantiles" in methods:
            logger.warning("Quantiles method defined but no parameters set. Method skipped.")
            methods.remove("quantiles")

    if len(methods) == 0:
        logger.warning("No method defined. Add default mean method")
        methods.add("mean")

    result = []
    for method in methods:
        if not average.is_algorithm_name_exists(method):
            logger.warning("Method name '%s' unknown. Method skipped.")
            continue

        try:
            algorithm = average.create_algorithm(method, options.cutoff, quantiles)
        except average.AlgorithmCreationError as e:
            logger.warning("Method skipped: %s", e)
            continue

        result.append(algorithm)

    return result


def parse_writer(input_images, options, algorithms):
    """Return a writer by using information from the command line"""
    output = options.output
    file_format = options.format

    if output:
        template = output
        if len(algorithms) > 1 and "{method_name}" not in template:
            # make sure the template will create multi files
            base, ext = os.path.splitext(template)
            template = base + "_{method_name}" + ext
    else:
        prefix = "{common_prefix}"
        suffix = ""
        if options.cutoff:
            suffix += "_cutoff_{cutoff}_std" % options.cutoff
        suffix += "_{image_count}_files.{file_format}"
        template = prefix + "{method_name}" + suffix

    formats = {
        "common_prefix": os.path.commonprefix(input_images),
        "image_count": len(input_images),
        "cutoff": options.cutoff,
        "file_format": file_format,
    }

    output = pyFAI.utils.stringutil.safe_format(template, formats)
    return average.MultiFilesAverageWriter(output, file_format)


class ShellAverageObserver(average.AverageObserver):
    """Display average processing using a shell progress bar"""

    def __init__(self):
        self.__bar = None
        self.__size = 40

    def image_loaded(self, fabio_image, image_index, images_count):
        if self.__bar is None:
            self.__bar = pyFAI.utils.shell.ProgressBar("Loading", images_count, self.__size)
        self.__bar.update(image_index, fabio_image.filename)

    def process_started(self):
        if self.__bar is not None:
            self.__bar.clear()
            self.__bar = None

    def algorithm_started(self, algorithm):
        if self.__bar is not None:
            self.__bar.clear()
            self.__bar = None

    def frame_processed(self, algorithm, frame_index, frames_count):
        if self.__bar is None:
            title = "Process %s" % algorithm.name
            self.__frames_count = frames_count + 1
            self.__bar = pyFAI.utils.shell.ProgressBar(title, self.__frames_count, self.__size)
        self.__bar.update(frame_index, "Feeding frames")

    def result_processing(self, algorithm):
        self.__bar.update(self.__frames_count - 1, "Computing result")

    def algorithm_finished(self, algorithm):
        self.__bar.clear()
        self.__bar = None
        print("%s reduction finished" % algorithm.name.capitalize())

    def process_finished(self):
        pass

    def clear(self):
        if self.__bar is not None:
            self.__bar.clear()


def main():
    usage = "pyFAI-average [options] [options] -o output.edf file1.edf file2.edf ..."
    version = "pyFAI-average version %s from %s" % (pyFAI.version, pyFAI.date)
    description = """
    This tool can be used to average out a set of dark current images using
    mean or median filter (along the image stack). One can also reject outliers
    be specifying a cutoff (remove cosmic rays / zingers from dark)
    """
    epilog = """It can also be used to merge many images from the same sample when using a small beam
    and reduce the spotty-ness of Debye-Sherrer rings. In this case the "max-filter" is usually
    recommended.
    """
    parser = ArgumentParser(usage=usage, description=description, epilog=epilog)
    parser.add_argument("-V", "--version", action='version', version=version)
    parser.add_argument("-o", "--output", dest="output",
                        type=str, default=None,
                        help="Output/ destination of average image")
    parser.add_argument("-m", "--method", dest="method",
                        type=str, default="",
                        help="Method used for averaging, can be 'mean' \
                        (default) or 'min', 'max', 'median', 'sum', 'quantiles'\
                        , 'cutoff', 'std'. Multiple filters can be defined with \
                        ',' separator.")
    parser.add_argument("-c", "--cutoff", dest="cutoff", type=float, default=None,
                        help="Take the mean of the average +/- cutoff * std_dev.")
    parser.add_argument("-F", "--format", dest="format", type=str, default="edf",
                        help="Output file/image format (by default EDF)")
    parser.add_argument("-d", "--dark", dest="dark", type=str, default=None,
                        help="Dark noise to be subtracted")
    parser.add_argument("-f", "--flat", dest="flat", type=str, default=None,
                        help="Flat field correction")
    parser.add_argument("-v", "--verbose", action="store_true", dest="verbose", default=None,
                        help="switch to verbose/debug mode")
    parser.add_argument("-q", "--quantiles", dest="quantiles", default=None,
                        help="average out between two quantiles -q 0.20-0.90")
    parser.add_argument("--monitor-name", dest="monitor_key", default=None,
                        help="Name of the monitor in the header of each input \
                        files. If defined the contribution of each input file \
                        is divided by the monitor. If the header does not \
                        contain or contains a wrong value, the contribution of \
                        the input file is ignored.\
                        On EDF files, values from 'counter_pos' can accessed by \
                        using the expected mnemonic. \
                        For example 'counter/bmon'.")
    parser.add_argument("--quiet", dest="verbose", default=None, action="store_false",
                        help="Only error messages are printed out")
    parser.add_argument("args", metavar='FILE', type=str, nargs='+',
                        help="Files to be processed")

    options = parser.parse_args()

    # logging
    if options.verbose is True:
        average.logger.setLevel(logging.DEBUG)
    elif options.verbose is False:
        average.logger.setLevel(logging.ERROR)
    else:
        average.logger.setLevel(logging.WARN)

    # shell output
    if options.verbose is not False:
        observer = ShellAverageObserver()
        # clean up the progress bar before displaying a log
        patch_logging_handler(observer.clear)
    else:
        observer = None

    # Analyze arguments and options
    images = pyFAI.utils.expand_args(options.args)

    if options.flat:
        flats = pyFAI.utils.expand_args([options.flat])
    else:
        flats = None

    if options.dark:
        darks = pyFAI.utils.expand_args([options.dark])
    else:
        darks = None

    algorithms = parse_algorithms(options)
    if len(algorithms) == 0:
        logger.warning("Configure process with a mean filter")
        algorithms = [average.MeanAveraging()]

    writer = parse_writer(images, options, algorithms)

    if images:
        process = average.Average()
        process.set_observer(observer)
        process.set_images(images)
        process.set_dark(darks)
        process.set_flat(flats)
        # average.set_correct_flat_from_dark(correct_flat_from_dark)
        process.set_monitor_name(options.monitor_key)
        process.set_pixel_filter(threshold=0, minimum=None, maximum=None)
        for algorithm in algorithms:
            process.add_algorithm(algorithm)
        process.set_writer(writer)
        process.process()
    else:
        logger.warning("No input file specified.")


if __name__ == "__main__":
    main()
