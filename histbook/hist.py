#!/usr/bin/env python

# Copyright (c) 2018, DIANA-HEP
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

import numbers
import threading

import numpy

import histbook.axis
import histbook.calc
import histbook.calc.spark
import histbook.export
import histbook.expr
import histbook.fill
import histbook.proj
import histbook.instr
import histbook.util
import histbook.vega

class Hist(histbook.fill.Fillable, histbook.proj.Projectable, histbook.export.Exportable, histbook.vega.PlottingChain):
    COUNTTYPE = numpy.float64

    @property
    def _source(self):
        return self

    @property
    def _chain(self):
        return ()

    def weight(self, expr):
        """Returns a copy of this histogram with ``expr`` as weights (for fluent construction)."""
        return Hist(*[x.relabel(x._original) for x in self._group + self._fixed + self._profile], weight=expr, defs=self._defs)

    @staticmethod
    def _copycontent(content):
        if content is None:
            return None
        elif isinstance(content, numpy.ndarray):
            return content.copy()
        else:
            return dict((n, Hist._copycontent(x)) for n, x in content.items())

    def copy(self):
        """Return an immediate copy of the histogram."""
        out = self.__class__.__new__(self.__class__)
        out.__dict__.update(self.__dict__)
        out._content = Hist._copycontent(self._content)
        return out

    def copyonfill(self):
        """Return a copy of the histogram whose content is copied if filled."""
        out = self.__class__.__new__(self.__class__)
        out.__dict__.update(self.__dict__)
        out._copyonfill = True
        return out

    def clear(self):
        """Effectively reset all bins to zero."""
        self._content = None

    def cleared(self):
        """Return a copy with all bins set to zero."""
        out = self.copyonfill()
        out._content = None
        out._copyonfill = False
        return out

    def __init__(self, *axis, **opts):
        u"""
        Parameters
        ----------
        *axis : :py:class:`Axis <histbook.axis.Axis>`
            axis or axes that define the independent and dependent variables of the histogram

        Keyword Arguments
        -----------------
        weight : ``None``, algebraic expression (lambda or string), or number
            if ``None`` *(default)*, data will be filled with weight ``1``; if an expression, weights are computed from the expression; if a number, weights are constant

        defs : ``None`` or dict of str \u2192 algebraic expression (lambda or string) or :py:class:`Expr <histbook.expr.Expr>`
            if not ``None``, definitions to use when computing expressions

        fill : ``None``, single Numpy array or dict of str \u2192 Numpy arrays
            if not ``None``, data to immediately fill after constructing the histogram; single Numpy array is only permitted if there's only one field
        """
        weight = opts.pop("weight", None)
        defs = opts.pop("defs", {})
        fill = opts.pop("fill", None)
        if len(opts) > 0:
            raise TypeError("unrecognized options for Hist: {0}".format(" ".join(opts)))

        self._defs = defs
        self._group = []
        self._fixed = []
        self._profile = []

        newaxis = []
        for old in axis:
            if isinstance(old, histbook.axis._nullaxis) or (hasattr(old, "_original") and hasattr(old, "_parsed")):
                newaxis.append(old)
            else:
                expr, label = histbook.expr.Expr.parse(old._expr, defs=defs, returnlabel=True)
                new = old.relabel(label)
                new._original = old._expr
                new._parsed = expr
                newaxis.append(new)

        self._goals = set()
        self._destination = [[]]
        self._lookup = {}
        def dest(goals):
            self._goals.update(set(goals))
            for goal in goals:
                if goal.goal not in self._lookup:
                    self._lookup[goal.goal] = []
                self._lookup[goal.goal].append(len(self._destination[0]))
                self._destination[0].append(None)

        dictindex = 0
        for new in newaxis:
            if isinstance(new, histbook.axis.GroupAxis):
                self._group.append(new)
                new._dictindex = dictindex
                dictindex += 1
                dest(new._goals(new._parsed))

        self._shape = []
        for new in newaxis:
            if isinstance(new, histbook.axis.FixedAxis):
                self._fixed.append(new)
                new._shapeindex = len(self._shape)
                self._shape.append(new.totbins)
                if not isinstance(new, histbook.axis._nullaxis):
                    dest(new._goals(new._parsed))

        self._shape.append(0)
        for new in newaxis:
            if isinstance(new, histbook.axis.ProfileAxis):
                self._profile.append(new)
                new._sumwxindex = self._shape[-1]
                new._sumwx2index = self._shape[-1] + 1
                self._shape[-1] += 2
                dest(new._goals(new._parsed))

        if weight is None:
            self._weightoriginal, self._weightparsed, self._weightlabel = None, None, None
            self._sumwindex = self._shape[-1]
            self._shape[-1] += 1
        
        else:
            self._weightoriginal = weight
            if isinstance(weight, (numbers.Real, numpy.integer, numpy.floating)):
                self._weightparsed, self._weightlabel = histbook.expr.Const(weight), str(weight)
            else:
                self._weightparsed, self._weightlabel = histbook.expr.Expr.parse(weight, defs=self._defs, returnlabel=True)
            self._sumwindex = self._shape[-1]
            self._sumw2index = self._shape[-1] + 1
            self._shape[-1] += 2
            dest([histbook.instr.CallGraphGoal(self._weightparsed),
                  histbook.instr.CallGraphGoal(histbook.expr.Call("numpy.multiply", self._weightparsed, self._weightparsed))])
            
        self._group = tuple(self._group)
        self._fixed = tuple(self._fixed)
        self._profile = tuple(self._profile)

        self._weight = weight
        self._shape = tuple(self._shape)
        self._content = None
        self._fields = None
        self._copyonfill = False

        if fill is not None:
            if not histbook.calc.spark.isspark(fill, {}) and not isinstance(fill, dict):
                if len(self._group + self._fixed + self._profile) == 1:
                    fill = {str((self._group + self._fixed + self._profile)[0]._parsed): fill}
                else:
                    raise TypeError("fill must be a dict for histograms of more than one axis")
            self.fill(fill)

    def __repr__(self, indent=", "):
        out = [repr(x) for x in self._group + self._fixed + self._profile]
        if self._weightlabel is not None:
            out.append("weight={0}".format(repr(self._weightlabel)))
        if len(self._defs) > 0:
            out.append("defs={" + ", ".join("{0}: {1}".format(repr(n), repr(str(x)) if isinstance(x, histbook.expr.Expr) else repr(x)) for n, x in self._defs.items()) + "}")
        return "Hist(" + indent.join(out) + ")"

    def __str__(self, indent=",\n     "):
        return self.__repr__(indent)

    @property
    def shape(self):
        """Shape of the Numpy array defining the content of the fixed-memory axes (:py:class:`FixedAxis <histbook.axis.FixedAxis>`) only."""
        return self._shape

    def _streamline(self, i, instructions):
        for instruction in instructions:
            if isinstance(instruction, histbook.instr.Export):
                if not hasattr(instruction, "destination"):
                    instruction.destination = []
                if instruction.goal in self._lookup:
                    for j in self._lookup[instruction.goal]:
                        instruction.destination.append((i, j))

        return instructions

    def fill(self, arrays=None, **more):
        u"""
        Fill the histogram: identify bins for independent variables, increase their counts by ``1`` or ``weight``, and increment any profile (dependent variable) means and errors in the means.

        All arrays must have the same length (one-dimensional shape). Numbers are treated as one-element arrays.

        Parameters
        ----------
        arrays : dict \u2192 Numpy array or number
            field values to use in the calculation of independent and dependent variables (axes)

        **more : dict \u2192 Numpy array or number
            more field values
        """
        if self._copyonfill:
            self._content = Hist._copycontent(self._content)
            self._copyonfill = False

        if histbook.calc.spark.isspark(arrays, more):
            # special SparkSQL
            wait = histbook.calc.spark.fillspark(self, arrays)
            self._prefill()
            wait()

        else:
            # standard Numpy
            if arrays is None:
                arrays = more
            elif len(more) == 0:
                pass
            else:
                arrays = histbook.util.ChainedDict(arrays, more)

            self._prefill()
            length = self._fill(arrays)
            self._postfill(arrays, length)

    def _prefill(self):
        if self._content is None:
            if len(self._group) == 0:
                self._content = numpy.zeros(self._shape, dtype=self.COUNTTYPE)
            else:
                self._content = {}

    def _postfill(self, arrays, length):
        j = len(self._group)
        step = 0
        indexes = None
        for axis in self._fixed:
            if step == 0:
                indexes = self._destination[0][j]
            elif step == 1:
                indexes = indexes.copy()
            if step > 0:
                numpy.multiply(indexes, self._shape[axis._shapeindex], indexes)
                numpy.add(indexes, self._destination[0][j], indexes)
            j += 1
            step += 1

        axissumx, axissumx2 = [], []
        for axis in self._profile:
            axissumx.append(self._destination[0][j])
            axissumx2.append(self._destination[0][j + 1])
            j += 2

        if self._weightparsed is None:
            weight = 1
            weight2 = None
        elif isinstance(self._weightparsed, histbook.expr.Const):
            weight = numpy.ones(length) * self._weightparsed.value
            weight2 = numpy.ones(length) * self._weightparsed.value**2
        else:
            weight = self._destination[0][j]
            weight2 = self._destination[0][j + 1]
            selection = numpy.isnan(weight)
            if selection.any():
                weight = weight.copy()
                weight2 = weight2.copy()
                weight[selection] = 0.0
                weight2[selection] = 0.0

        def fillblock(content, indexes, axissumx, axissumx2, weight, weight2):
            for sumx, sumx2, axis in zip(axissumx, axissumx2, self._profile):
                if indexes is None:
                    indexes = numpy.ma.zeros(len(sumx), dtype=histbook.calc.INDEXTYPE)
                if weight2 is not None:
                    selection = numpy.ma.getmask(indexes)
                    if selection is not numpy.ma.nomask:
                        selection = numpy.bitwise_not(selection)
                        weight = weight[selection]
                        weight2 = weight2[selection]
                numpy.add.at(content.reshape((-1, self._shape[-1]))[:, axis._sumwxindex], indexes.compressed(), sumx * weight)
                numpy.add.at(content.reshape((-1, self._shape[-1]))[:, axis._sumwx2index], indexes.compressed(), sumx2 * weight)

            if weight2 is None:
                if indexes is None:
                    content.reshape((-1, self._shape[-1]))[:, self._sumwindex] += (1 if length is None else length) * weight
                else:
                    numpy.add.at(content.reshape((-1, self._shape[-1]))[:, self._sumwindex], indexes.compressed(), weight)
            else:
                if indexes is None:
                    indexes = numpy.ma.zeros(len(weight), dtype=histbook.calc.INDEXTYPE)
                selection = numpy.ma.getmask(indexes)
                if selection is not numpy.ma.nomask:
                    selection = numpy.bitwise_not(selection)
                    weight = weight[selection]
                    weight2 = weight2[selection]
                numpy.add.at(content.reshape((-1, self._shape[-1]))[:, self._sumwindex], indexes.compressed(), weight)
                numpy.add.at(content.reshape((-1, self._shape[-1]))[:, self._sumw2index], indexes.compressed(), weight2)

        def filldict(j, content, indexes, axissumx, axissumx2, weight, weight2, allselection):
            if j == len(self._group):
                fillblock(content, indexes, axissumx, axissumx2, weight, weight2)

            else:
                uniques, inverse = self._destination[0][j]
                for idx, unique in enumerate(uniques):
                    if allselection is None:
                        selection = (inverse == idx)
                    else:
                        selection = (inverse[allselection] == idx)
                    
                    if unique not in content:
                        if j + 1 == len(self._group):
                            content[unique] = numpy.zeros(self._shape, dtype=self.COUNTTYPE)
                        else:
                            content[unique] = {}

                    subcontent = content[unique]
                    if indexes is None:
                        subindexes = numpy.ma.zeros(numpy.count_nonzero(selection), dtype=histbook.calc.INDEXTYPE)
                    else:
                        subindexes = indexes[selection]
                    subaxissumx = [x[selection] for x in axissumx]
                    subaxissumx2 = [x[selection] for x in axissumx2]
                    if weight2 is None:
                        subweight, subweight2 = weight, weight2
                    else:
                        subweight = weight[selection]
                        subweight2 = weight2[selection]

                    if allselection is None:
                        suballselection = selection
                    else:
                        suballselection = allselection.copy()
                        suballselection[inverse != idx] = False

                    filldict(j + 1, subcontent, subindexes, subaxissumx, subaxissumx2, subweight, subweight2, suballselection)

        filldict(0, self._content, indexes, axissumx, axissumx2, weight, weight2, None)
            
        for j in range(len(self._destination[0])):
            self._destination[0][j] = None

    def __add__(self, other):
        if not isinstance(other, Hist):
            raise TypeError("histograms can only be added to other histograms")

        if self._group + self._fixed + self._profile != other._group + other._fixed + other._profile:
            raise TypeError("histograms can only be added to other histograms with the same axis specifications")

        def add(selfcontent, othercontent):
            if selfcontent is None and othercontent is None:
                return None

            elif selfcontent is None:
                return Hist._copycontent(othercontent)

            elif othercontent is None:
                return Hist._copycontent(selfcontent)

            elif isinstance(selfcontent, numpy.ndarray) and isinstance(othercontent, numpy.ndarray):
                return selfcontent + othercontent

            else:
                assert isinstance(selfcontent, dict) and isinstance(othercontent, dict)
                out = {}
                for n in selfcontent:
                    if n in othercontent:
                        out[n] = add(selfcontent[n], othercontent[n])
                    else:
                        out[n] = selfcontent[n]
                for n in othercontent:
                    if n not in selfcontent:
                        out[n] = othercontent[n]
                return out

        out = self.__class__.__new__(self.__class__)
        out.__dict__.update(self.__dict__)
        out._content = add(self._content, other._content)
        return out

    def __iadd__(self, other):
        if not isinstance(other, Hist):
            raise TypeError("histograms can only be added to other histograms")

        if self._group + self._fixed + self._profile != other._group + other._fixed + other._profile:
            raise TypeError("histograms can only be added to other histograms with the same axis specifications")

        def add(selfcontent, othercontent):
            assert isinstance(selfcontent, dict) and isinstance(othercontent, dict)
            for n in selfcontent:
                if n in othercontent:
                    if isinstance(selfcontent[n], numpy.ndarray):
                        selfcontent[n] += othercontent[n]
                    else:
                        add(selfcontent[n], othercontent[n])
            for n in othercontent:
                if n not in selfcontent:
                    selfcontent[n] = Hist._copycontent(othercontent[n])

        if other._content is None:
            pass

        elif self._content is None:
            self._content = Hist._copycontent(other._content)

        elif isinstance(self._content, numpy.ndarray):
            self._content += other._content

        else:
            add(self._content, other._content)

        return self

    def __mul__(self, value):
        if not isinstance(value, (numbers.Real, numpy.integer, numpy.floating)):
            raise TypeError("Hist can only be multiplied by a scalar number.")

        def recurse(content):
            if isinstance(content, dict):
                return dict((n, recurse(x)) for n, x in content.items())
            else:
                return content * value

        out = self.__class__.__new__(self.__class__)
        out.__dict__.update(self.__dict__)
        out._content = recurse(self._content)
        return out

    def __rmul__(self, value):
        return self.__mul__(value)

    def __imul__(self, value):
        if not isinstance(value, (numbers.Real, numpy.integer, numpy.floating)):
            raise TypeError("Hist can only be multiplied by a scalar number.")

        def recurse(content):
            if isinstance(content, dict):
                for x in content.values():
                    recurse(x)
            else:
                content *= 0

        return self

    @staticmethod
    def group(by="source", **hists):
        """
        Combine histograms, maintaining their distinctiveness by adding a new categorical axis to each.

        To combine histograms by adding bins, just use the ``+`` operator.

        Parameters
        ----------
        by : string
            name of the new axis (must not already exist)

        **hists : :py:class:`Hist <histbook.hist.Hist>`
            histograms to combine (must have the same axes)
        """
        if any(not isinstance(x, Hist) for x in hists.values()):
            raise TypeError("only histograms can be grouped with other histograms")

        axis = None
        hist = None
        for x in hists.values():
            hist = x
            if axis is None:
                axis = x._group + x._fixed + x._profile
            elif axis != x._group + x._fixed + x._profile:
                raise TypeError("histograms can only be grouped with the same axis specifications")
            
        if axis is None:
            raise ValueError("at least one histogram must be provided")

        if histbook.axis.groupby(by) in axis:
            raise ValueError("groupby({0}) already exists in these histograms; use hist.togroup(other) to add to a group".format(repr(by)))

        weight = None
        for x in hists.values():
            if weight is None:
                weight = x._weight
            elif x._weight is None:
                raise TypeError("histograms can only be grouped with the same weight specifications")
            elif weight != x._weight:
                weight = 1.0

        defs = {}
        for x in hists.values():
            defs.update(x._defs)

        out = Hist(*([histbook.axis.groupby(by)] + [x.relabel(x._original) for x in hist._group + hist._fixed + hist._profile]), weight=weight, defs=defs)
        out._content = {}
        for n, x in hists.items():
            out._content[n] = Hist._copycontent(x._content)
        return out

    def togroup(**hists):
        u"""
        Adds histograms to the :py:class:`groupby <histbook.axis.groupby>` that is the first axis.

        Histograms created with :py:meth:`Hist.group <histbook.hist.Hist.group>` have a first axis that is a :py:class:`groupby <histbook.axis.groupby>`.

        Keyword Arguments
        -----------------
        **hists : dict of str \u2192 :py:class:`Hist <histbook.hist.Hist>`
            histograms to add to the existing group
        """
        if len(self._group) == 0 or not isinstance(self._group[0], histbook.axis.groupby):
            raise ValueError("togroup can only be used on histograms whose first axis is a groupby")

        if any(not isinstance(x, Hist) for x in hists.values()):
            raise TypeError("only histograms can be grouped")

        for x in hists.values():
            if self._group[1:] + self._fixed + self._profile != x._group + x._fixed + x._profile:
                raise TypeError("histograms can only be grouped with the same axis specifications")

        def add(selfcontent, othercontent):
            assert isinstance(selfcontent, dict) and isinstance(othercontent, dict)
            for n in selfcontent:
                if n in othercontent:
                    if isinstance(selfcontent[n], numpy.ndarray):
                        selfcontent[n] += othercontent[n]
                    else:
                        add(selfcontent[n], othercontent[n])
            for n in othercontent:
                if n not in selfcontent:
                    selfcontent[n] = Hist._copycontent(othercontent[n])

        for n, x in hists.items():
            if n in self._content:
                add(self._content[n], other._content)
            else:
                self._content[n] = Hist._copycontent(other._content)

    def __getstate__(self):
        packed = tuple(x._pack() for x in self._group + self._fixed + self._profile)
        return (packed, self._weight, None if len(self._defs) == 0 else self._defs, self._content)

    def __setstate__(self, state):
        packed, weight, defs, content = state
        self.__init__(*[histbook.axis.Axis._unpack(x) for x in packed], weight=weight, defs=({} if defs is None else defs))
        self._content = content
