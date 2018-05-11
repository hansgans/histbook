#!/usr/bin/env python

# Copyright (c) 2017, DIANA-HEP
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# 
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# 
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import histbook.expr
import histbook.stmt

import numpy
INDEXTYPE = numpy.int32

library = {}

library["numpy.add"] = numpy.add

def histbook_sparse(closedlow):
    def sparse(values, binwidth, origin):
        if origin == 0:
            indexes = numpy.true_divide(values, float(binwidth))
        else:
            indexes = values - float(origin)
            numpy.true_divide(indexes, float(binwidth), indexes)

        if closedlow:
            numpy.floor(indexes, indexes)
        else:
            numpy.ceil(indexes, indexes)
            numpy.subtract(indexes, 1, indexes)

        ok = numpy.isnan(indexes)
        numpy.logical_not(ok, ok)

        if ok.all():
            uniques, inverse = numpy.unique(indexes, return_inverse=True)
            inverse = inverse.astype(INDEXTYPE)
        else:
            uniques, okinverse = numpy.unique(indexes[ok], return_inverse=True)
            inverse = numpy.ones(indexes.shape, dtype=INDEXTYPE)
            numpy.multiply(inverse, -1, inverse)
            inverse[ok] = okinverse
        return uniques, inverse

    return sparse

library["histbook.sparseL"] = histbook_sparse(True)
library["histbook.sparseH"] = histbook_sparse(False)
    
def histbook_bin(underflow, overflow, nanflow, closedlow):
    if nanflow:
        nanindex = (1 if underflow else 0) + (1 if overflow else 0)
    else:
        nanindex = numpy.ma.masked

    if underflow:
        shift = 1
    else:
        shift = 0

    def bin(values, numbins, low, high):
        indexes = values - float(low)
        numpy.multiply(indexes, numbins / (high - low), indexes)

        if closedlow:
            numpy.floor(indexes, indexes)
            if shift != 0:
                numpy.add(indexes, shift, indexes)
        else:
            numpy.ceil(indexes, indexes)
            numpy.add(indexes, shift - 1, indexes)

        out = numpy.ma.array(indexes, dtype=INDEXTYPE)
        with numpy.errstate(invalid="ignore"):
            if underflow:
                numpy.maximum(out, 0, out)
            else:
                out[out <= 0] = numpy.ma.masked
            if overflow:
                numpy.minimum(out, shift + numbins, out)
            else:
                out[out >= (numbins + shift)] = numpy.ma.masked
            out[numpy.isnan(indexes)] = nanindex + numbins
        return out

    return bin

library["histbook.binUONL"] = histbook_bin(True, True, True, True)
library["histbook.binUONH"] = histbook_bin(True, True, True, False)
library["histbook.binUO_L"] = histbook_bin(True, True, False, True)
library["histbook.binUO_H"] = histbook_bin(True, True, False, False)
library["histbook.binU_NL"] = histbook_bin(True, False, True, True)
library["histbook.binU_NH"] = histbook_bin(True, False, True, False)
library["histbook.binU__L"] = histbook_bin(True, False, False, True)
library["histbook.binU__H"] = histbook_bin(True, False, False, False)
library["histbook.bin_ONL"] = histbook_bin(False, True, True, True)
library["histbook.bin_ONH"] = histbook_bin(False, True, True, False)
library["histbook.bin_O_L"] = histbook_bin(False, True, False, True)
library["histbook.bin_O_H"] = histbook_bin(False, True, False, False)
library["histbook.bin__NL"] = histbook_bin(False, False, True, True)
library["histbook.bin__NH"] = histbook_bin(False, False, True, False)
library["histbook.bin___L"] = histbook_bin(False, False, False, True)
library["histbook.bin___H"] = histbook_bin(False, False, False, False)

def histbook_intbin(underflow, overflow):
    if underflow:
        shift = 1
    else:
        shift = 0

    def intbin(values, min, max):
        indexes = numpy.ma.array((values + (shift - min)), dtype=INDEXTYPE)

        if underflow:
            numpy.maximum(indexes, 0, indexes)
        else:
            indexes[indexes < 0] = numpy.ma.masked

        if overflow:
            numpy.minimum(indexes, (shift + 1 + max - min), indexes)
        else:
            indexes[indexes > (shift + max - min)] = numpy.ma.masked

        return indexes

    return intbin

library["histbook.intbinUO"] = histbook_intbin(True, True)
library["histbook.intbinU_"] = histbook_intbin(True, False)
library["histbook.intbin_O"] = histbook_intbin(False, True)
library["histbook.intbin__"] = histbook_intbin(False, False)

def histbook_partition(underflow, overflow, nanflow, closedlow):
    if nanflow:
        nanindex = (1 if underflow else 0) + (1 if overflow else 0)
    else:
        nanindex = numpy.ma.masked
    if underflow:
        shift = 1
    else:
        shift = 0
    def partition(values, edges):
        indexes = numpy.ma.array(numpy.digitize(values, edges), dtype=INDEXTYPE)
        if not closedlow:
            indexes[numpy.isin(values, edges)] -= 1
        if nanflow:
            indexes[numpy.isnan(values)] = len(edges) + 1
        else:
            indexes[numpy.isnan(values)] = numpy.ma.masked
        if not overflow:
            indexes[indexes == len(edges)] = numpy.ma.masked
        if not underflow:
            indexes[indexes == 0] = numpy.ma.masked
            numpy.subtract(indexes, 1, indexes)
        return indexes
    return partition

library["histbook.partitionUONL"] = histbook_partition(True, True, True, True)
library["histbook.partitionUONH"] = histbook_partition(True, True, True, False)
library["histbook.partitionUO_L"] = histbook_partition(True, True, False, True)
library["histbook.partitionUO_H"] = histbook_partition(True, True, False, False)
library["histbook.partitionU_NL"] = histbook_partition(True, False, True, True)
library["histbook.partitionU_NH"] = histbook_partition(True, False, True, False)
library["histbook.partitionU__L"] = histbook_partition(True, False, False, True)
library["histbook.partitionU__H"] = histbook_partition(True, False, False, False)
library["histbook.partition_ONL"] = histbook_partition(False, True, True, True)
library["histbook.partition_ONH"] = histbook_partition(False, True, True, False)
library["histbook.partition_O_L"] = histbook_partition(False, True, False, True)
library["histbook.partition_O_H"] = histbook_partition(False, True, False, False)
library["histbook.partition__NL"] = histbook_partition(False, False, True, True)
library["histbook.partition__NH"] = histbook_partition(False, False, True, False)
library["histbook.partition___L"] = histbook_partition(False, False, False, True)
library["histbook.partition___H"] = histbook_partition(False, False, False, False)

library["histbook.cut"] = lambda values: numpy.ma.array(values, dtype=INDEXTYPE)

def calculate(expr, symbols):
    if isinstance(expr, (histbook.expr.Name, histbook.expr.Predicate)):
        return symbols[expr.value]

    elif isinstance(expr, histbook.expr.Const):
        return expr.value

    elif isinstance(expr, histbook.expr.Call) and expr.fcn in library:
        return library[expr.fcn](*(calculate(arg, symbols) for arg in expr.args))
            
    else:
        raise NotImplementedError(expr)
