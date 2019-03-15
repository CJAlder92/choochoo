
from collections import defaultdict, namedtuple
from itertools import groupby
from json import loads
from logging import getLogger
from random import uniform

from sqlalchemy import inspect, select, alias, and_, distinct, func, not_, or_
from sqlalchemy.sql.functions import count

from ch2.stoats.calculate.activity import ActivityCalculator
from ch2.stoats.read.segment import SegmentReader
from . import Statistics, UniProcCalculator
from ..names import LONGITUDE, LATITUDE, ACTIVE_DISTANCE
from ..read.activity import ActivityReader
from ...arty import MatchType
from ...arty.spherical import SQRTree
from ...lib.date import to_time, local_date_to_time
from ...lib.dbscan import DBSCAN
from ...lib.optimizn import expand_max
from ...squeal import ActivityJournal, ActivityGroup, Constant, ActivitySimilarity, ActivityNearby, StatisticName, \
    StatisticJournal, StatisticJournalFloat, Timestamp


log = getLogger(__name__)
Nearby = namedtuple('Nearby', 'constraint, activity_group, border, start, finish, '
                              'latitude, longitude, height, width, fraction')


class SimilarityStatistics(Statistics):

    def _on_init(self, *args, **kargs):
        super()._on_init(*args, **kargs)
        nearby = self._karg('nearby')
        with self._db.session_context() as s:
            self._config = Nearby(**loads(Constant.get(s, nearby).at(s).value))
        self._log.info('%s: %s' % (nearby, self._config))

    def run(self):

        rtree = SQRTree(default_match=MatchType.OVERLAP, default_border=self._config.border)

        with self._db.session_context() as s:
            if self._force():
                self._delete(s)
            else:
                if self._no_new_data(s):
                    return
            n_points = defaultdict(lambda: 0)
            self._prepare(s, rtree, n_points, 30000)
            n_overlaps = defaultdict(lambda: defaultdict(lambda: 0))
            new_ids, affected_ids = self._count_overlaps(s, rtree, n_points, n_overlaps, 10000)
            # this clears itself beforehand
            # use explicit class to distinguish from subclasses (which compare against this)
            with Timestamp(owner=SimilarityStatistics, constraint=self._config.constraint). \
                    on_success(self._log, s):
                self._save(s, new_ids, affected_ids, n_points, n_overlaps, 10000)

    def _delete(self, s):
        start, finish = self._start_finish()
        self._log.warning(f'Deleting similarity data for {self._config.constraint} from {start} to {finish}')
        activity_ids = s.query(ActivityJournal.id)
        if start:
            activity_ids = activity_ids.filter(ActivityJournal.start >= local_date_to_time(start))
        if finish:
            activity_ids = activity_ids.filter(ActivityJournal.start < local_date_to_time(finish))
        s.query(ActivitySimilarity). \
            filter(ActivitySimilarity.constraint == self._config.constraint,
                   or_(ActivitySimilarity.activity_journal_lo_id.in_(activity_ids.cte()),
                       ActivitySimilarity.activity_journal_hi_id.in_(activity_ids.cte()))). \
            delete(synchronize_session=False)
        s.commit()  # session was new so above is not an issue (this just to be completely sure)

    def _latest_timestamp(self, s, owner):
        return s.query(Timestamp). \
            filter(Timestamp.owner == owner,
                   Timestamp.constraint == self._config.constraint,
                   Timestamp.key == None).one_or_none()

    def _no_new_data(self, s):
        prev = self._latest_timestamp(s, SimilarityStatistics)
        if not prev:
            return False
        prev_ids = s.query(Timestamp.key). \
            filter(Timestamp.owner == ActivityReader,
                   Timestamp.constraint == None,
                   Timestamp.time < prev.time).cte()
        return s.query(count(ActivityJournal.id)). \
            join(ActivityGroup). \
            filter(ActivityGroup.name == self._config.activity_group,
                   not_(ActivityGroup.id.in_(prev_ids))).scalar()

    def _save(self, s, new_ids, affected_ids, n_points, n_overlaps, delta):
        distances = dict((s.source.id, s.value)
                         for s in s.query(StatisticJournalFloat).
                         join(StatisticName).
                         filter(StatisticName.name == ACTIVE_DISTANCE,
                                StatisticName.owner == ActivityReader).all())
        n = 0
        for lo in affected_ids:
            add_lo, d_lo = lo in new_ids, distances.get(lo, None)
            if d_lo:
                for hi in (id for id in affected_ids if id > lo):
                    d_hi = distances.get(hi, None)
                    if d_hi and (add_lo or hi in new_ids):
                        if lo in new_ids and hi not in new_ids:
                            # hi already existed so was added first
                            n_max = n_points[hi]
                            d_factor = d_hi / d_lo if d_hi < d_lo else 1
                        else:
                            # both new and ordered lo-hi, or hi new and last
                            n_max = n_points[lo]
                            d_factor = d_lo / d_hi if d_lo < d_hi else 1
                        s.add(ActivitySimilarity(constraint=self._config.constraint,
                                                 activity_journal_lo_id=lo, activity_journal_hi_id=hi,
                                                 similarity=(n_overlaps[lo][hi] / n_max) * d_factor))
                        n += 1
                        if n % delta == 0:
                            self._log.info('Saved %d for %s' % (n, self._config.constraint))
        if n % delta:
            self._log.info('Saved %d for %s' % (n, self._config.constraint))

    def _prepare(self, s, rtree, n_points, delta):
        n = 0
        for aj_id_in, lon, lat in self._aj_lon_lat(s, new=False):
            posn = [(lon, lat)]
            rtree[posn] = aj_id_in
            n_points[aj_id_in] += 1
            n += 1
            if n % delta == 0:
                self._log.info('Loaded %s points for %s' % (n, self._config.constraint))
        if n % delta:
            self._log.info('Loaded %s points for %s' % (n, self._config.constraint))

    def _count_overlaps(self, s, rtree, n_points, n_overlaps, delta):
        new_aj_ids, affected_aj_ids, n = [], set(), 0
        for aj_id_in, aj_lon_lats in groupby(self._aj_lon_lat(s, new=True), key=lambda aj_lon_lat: aj_lon_lat[0]):
            aj_lon_lats = list(aj_lon_lats)  # reuse below
            seen_posns = set()
            new_aj_ids.append(aj_id_in)
            affected_aj_ids.add(aj_id_in)
            for _, lon, lat in aj_lon_lats:
                posn = [(lon, lat)]
                for other_posn, aj_id_out in rtree.get_items(posn):
                    if other_posn not in seen_posns:
                        lo, hi = min(aj_id_in, aj_id_out), max(aj_id_in, aj_id_out)  # ordered pair
                        affected_aj_ids.add(aj_id_out)
                        n_overlaps[lo][hi] += 1
                        seen_posns.add(other_posn)
            for _, lon, lat in aj_lon_lats:  # adding after avoids matching ourselves
                posn = [(lon, lat)]
                rtree[posn] = aj_id_in
                n_points[aj_id_in] += 1
                n += 1
                if n % delta == 0:
                    self._log.info('Measured %s points for %s' % (n, self._config.constraint))
        if n % delta:
            self._log.info('Measured %s points for %s' % (n, self._config.constraint))
        return new_aj_ids, affected_aj_ids

    def _aj_lon_lat(self, s, new=True):

        start = to_time(self._config.start)
        finish = to_time(self._config.finish)

        lat = s.query(StatisticName.id).filter(StatisticName.name == LATITUDE).scalar()
        lon = s.query(StatisticName.id).filter(StatisticName.name == LONGITUDE).scalar()
        agroup = s.query(ActivityGroup.id).filter(ActivityGroup.name == self._config.activity_group).scalar()

        sj_lat = inspect(StatisticJournal).local_table
        sj_lon = alias(inspect(StatisticJournal).local_table)
        sjf_lat = inspect(StatisticJournalFloat).local_table
        sjf_lon = alias(inspect(StatisticJournalFloat).local_table)
        aj = inspect(ActivityJournal).local_table
        ns = inspect(ActivitySimilarity).local_table

        existing_lo = select([ns.c.activity_journal_lo_id]). \
            where(ns.c.constraint == self._config.constraint)
        existing_hi = select([ns.c.activity_journal_hi_id]). \
            where(ns.c.constraint == self._config.constraint)
        existing = existing_lo.union(existing_hi).cte()

        stmt = select([sj_lat.c.source_id, sjf_lon.c.value, sjf_lat.c.value]). \
            select_from(sj_lat).select_from(sj_lon).select_from(sjf_lat).select_from(sjf_lat).select_from(aj). \
            where(and_(sj_lat.c.source_id == sj_lon.c.source_id,  # same source
                       sj_lat.c.time == sj_lon.c.time,            # same time
                       sj_lat.c.source_id == aj.c.id,             # and associated with an activity
                       aj.c.activity_group_id == agroup,          # of the right group
                       sj_lat.c.id == sjf_lat.c.id,               # lat sub-class
                       sj_lon.c.id == sjf_lon.c.id,               # lon sub-class
                       sj_lat.c.statistic_name_id == lat,         # lat name
                       sj_lon.c.statistic_name_id == lon,         # lon name
                       sj_lat.c.time >= start.timestamp(),        # time limits
                       sj_lat.c.time < finish.timestamp(),
                       sjf_lat.c.value > self._config.latitude - self._config.height / 2,
                       sjf_lat.c.value < self._config.latitude + self._config.height / 2,
                       sjf_lon.c.value > self._config.longitude - self._config.width / 2,
                       sjf_lon.c.value < self._config.longitude + self._config.width / 2))

        if new:
            stmt = stmt.where(func.not_(sj_lat.c.source_id.in_(existing)))
        else:
            stmt = stmt.where(sj_lat.c.source_id.in_(existing))
        stmt = stmt.order_by(sj_lat.c.source_id)  # needed for seen logic
        yield from s.connection().execute(stmt)


class SimilarityCalculator(UniProcCalculator):

    def __init__(self, *args, nearby=None, **kargs):
        self.nearby_ref = self._assert('nearby', nearby)
        super().__init__(*args, **kargs)

    def _startup(self, s):
        self.nearby = Nearby(**loads(Constant.get(s, self.nearby_ref).at(s).value))
        log.info(f'{self.nearby_ref}: {self.nearby}')
        log.info(f'Reducing to {int(0.5 + 100 * self.nearby.fraction):d}%')

    def _missing(self, s):
        prev = Timestamp.get(s, self.owner_out, self.nearby.constraint)
        if not prev:
            return [True]
        prev_ids = s.query(Timestamp.key). \
            filter(Timestamp.owner == self.owner_in,
                   Timestamp.constraint == None,
                   Timestamp.time < prev.time).cte()
        if s.query(count(ActivityJournal.id)). \
            join(ActivityGroup). \
            filter(ActivityGroup.name == self.nearby.activity_group,
                   not_(ActivityGroup.id.in_(prev_ids))).scalar():
            return [True]
        else:
            return []

    def _delete(self, s):
        start, finish = self._start_finish()
        log.warning(f'Deleting similarity data for {self.nearby.constraint} from {start} to {finish}')
        activity_ids = s.query(ActivityJournal.id)
        if start:
            activity_ids = activity_ids.filter(ActivityJournal.start >= local_date_to_time(start))
        if finish:
            activity_ids = activity_ids.filter(ActivityJournal.start < local_date_to_time(finish))
        s.query(ActivitySimilarity). \
            filter(ActivitySimilarity.constraint == self.nearby.constraint,
                   or_(ActivitySimilarity.activity_journal_lo_id.in_(activity_ids.cte()),
                       ActivitySimilarity.activity_journal_hi_id.in_(activity_ids.cte()))). \
            delete(synchronize_session=False)
        s.commit()

    def _run_one(self, s, missed):
        rtree = SQRTree(default_match=MatchType.OVERLAP, default_border=self.nearby.border)
        n_points = defaultdict(lambda: 0)
        self._prepare(s, rtree, n_points, 30000)
        n_overlaps = defaultdict(lambda: defaultdict(lambda: 0))
        new_ids, affected_ids = self._count_overlaps(s, rtree, n_points, n_overlaps, 10000)
        # this clears itself beforehand
        # use explicit class to distinguish from subclasses (which compare against this)
        with Timestamp(owner=self.owner_out, constraint=self.nearby.constraint).on_success(log, s):
            self._save(s, new_ids, affected_ids, n_points, n_overlaps, 10000)

    def _prepare(self, s, rtree, n_points, delta):
        n = 0
        for aj_id_in, lon, lat in self._aj_lon_lat(s, new=False):
            posn = [(lon, lat)]
            rtree[posn] = aj_id_in
            n_points[aj_id_in] += 1
            n += 1
            if n % delta == 0:
                log.info('Loaded %s points for %s' % (n, self.nearby.constraint))
        if n % delta:
            log.info('Loaded %s points for %s' % (n, self.nearby.constraint))

    def _count_overlaps(self, s, rtree, n_points, n_overlaps, delta):
        new_aj_ids, affected_aj_ids, n, no = [], set(), 0, 0
        for aj_id_in, aj_lon_lats in groupby(self._aj_lon_lat(s, new=True), key=lambda aj_lon_lat: aj_lon_lat[0]):
            aj_lon_lats = list(self._filter(aj_lon_lats))  # reuse below
            seen_posns = set()
            new_aj_ids.append(aj_id_in)
            affected_aj_ids.add(aj_id_in)
            for _, lon, lat in aj_lon_lats:
                posn = [(lon, lat)]
                for other_posn, aj_id_out in rtree.get_items(posn):
                    if other_posn not in seen_posns:
                        lo, hi = min(aj_id_in, aj_id_out), max(aj_id_in, aj_id_out)  # ordered pair
                        affected_aj_ids.add(aj_id_out)
                        n_overlaps[lo][hi] += 1
                        no += 1
                        seen_posns.add(other_posn)
            for _, lon, lat in aj_lon_lats:  # adding after avoids matching ourselves
                posn = [(lon, lat)]
                rtree[posn] = aj_id_in
                n_points[aj_id_in] += 1
                n += 1
                if n % delta == 0:
                    log.info(f'Measured {n} points for {self.nearby.constraint} ({no} overlaps)')
        if n % delta:
            log.info('Measured %s points for %s' % (n, self.nearby.constraint))
        return new_aj_ids, affected_aj_ids

    def _filter(self, lon_lats):
        for lon_lat in lon_lats:
            if uniform(0, 1 / self.nearby.fraction) < 1:
                yield lon_lat

    def _aj_lon_lat(self, s, new=True):

        start = to_time(self.nearby.start)
        finish = to_time(self.nearby.finish)

        lat = s.query(StatisticName.id).filter(StatisticName.name == LATITUDE).scalar()
        lon = s.query(StatisticName.id).filter(StatisticName.name == LONGITUDE).scalar()
        agroup = s.query(ActivityGroup.id).filter(ActivityGroup.name == self.nearby.activity_group).scalar()

        sj_lat = inspect(StatisticJournal).local_table
        sj_lon = alias(inspect(StatisticJournal).local_table)
        sjf_lat = inspect(StatisticJournalFloat).local_table
        sjf_lon = alias(inspect(StatisticJournalFloat).local_table)
        aj = inspect(ActivityJournal).local_table
        ns = inspect(ActivitySimilarity).local_table

        existing_lo = select([ns.c.activity_journal_lo_id]). \
            where(ns.c.constraint == self.nearby.constraint)
        existing_hi = select([ns.c.activity_journal_hi_id]). \
            where(ns.c.constraint == self.nearby.constraint)
        existing = existing_lo.union(existing_hi).cte()

        stmt = select([sj_lat.c.source_id, sjf_lon.c.value, sjf_lat.c.value]). \
            select_from(sj_lat).select_from(sj_lon).select_from(sjf_lat).select_from(sjf_lat).select_from(aj). \
            where(and_(sj_lat.c.source_id == sj_lon.c.source_id,  # same source
                       sj_lat.c.time == sj_lon.c.time,            # same time
                       sj_lat.c.source_id == aj.c.id,             # and associated with an activity
                       aj.c.activity_group_id == agroup,          # of the right group
                       sj_lat.c.id == sjf_lat.c.id,               # lat sub-class
                       sj_lon.c.id == sjf_lon.c.id,               # lon sub-class
                       sj_lat.c.statistic_name_id == lat,         # lat name
                       sj_lon.c.statistic_name_id == lon,         # lon name
                       sj_lat.c.time >= start.timestamp(),        # time limits
                       sj_lat.c.time < finish.timestamp(),
                       sjf_lat.c.value > self.nearby.latitude - self.nearby.height / 2,
                       sjf_lat.c.value < self.nearby.latitude + self.nearby.height / 2,
                       sjf_lon.c.value > self.nearby.longitude - self.nearby.width / 2,
                       sjf_lon.c.value < self.nearby.longitude + self.nearby.width / 2))

        if new:
            stmt = stmt.where(func.not_(sj_lat.c.source_id.in_(existing)))
        else:
            stmt = stmt.where(sj_lat.c.source_id.in_(existing))
        stmt = stmt.order_by(sj_lat.c.source_id)  # needed for seen logic
        yield from s.connection().execute(stmt)

    def _save(self, s, new_ids, affected_ids, n_points, n_overlaps, delta):
        distances = dict((s.source.id, s.value)
                         for s in s.query(StatisticJournalFloat).
                         join(StatisticName).
                         filter(StatisticName.name == ACTIVE_DISTANCE,
                                StatisticName.owner == self.owner_in).all())  # todo - another owner
        n = 0
        for lo in affected_ids:
            add_lo, d_lo = lo in new_ids, distances.get(lo, None)
            if d_lo:
                for hi in (id for id in affected_ids if id > lo):
                    d_hi = distances.get(hi, None)
                    if d_hi and (add_lo or hi in new_ids):
                        if lo in new_ids and hi not in new_ids:
                            # hi already existed so was added first
                            n_max = n_points[hi]
                            d_factor = d_hi / d_lo if d_hi < d_lo else 1
                        else:
                            # both new and ordered lo-hi, or hi new and last
                            n_max = n_points[lo]
                            d_factor = d_lo / d_hi if d_lo < d_hi else 1
                        s.add(ActivitySimilarity(constraint=self.nearby.constraint,
                                                 activity_journal_lo_id=lo, activity_journal_hi_id=hi,
                                                 similarity=(n_overlaps[lo][hi] / n_max) * d_factor))
                        n += 1
                        if n % delta == 0:
                            log.info('Saved %d for %s' % (n, self.nearby.constraint))
        if n % delta:
            log.info('Saved %d for %s' % (n, self.nearby.constraint))


class NearbySimilarityDBSCAN(DBSCAN):

    def __init__(self, log, s, constraint, epsilon, minpts):
        super().__init__(log, epsilon, minpts)
        self.__s = s
        self.__constraint = constraint
        self.__max_similarity = self.__s.query(func.max(ActivitySimilarity.similarity)). \
            filter(ActivitySimilarity.constraint == constraint).scalar()
        # self._log.info('Max similarity %.2f' % self.__max_similarity)

    def run(self):
        candidates = set(x[0] for x in
                         self.__s.query(distinct(ActivitySimilarity.activity_journal_lo_id)).
                         filter(ActivitySimilarity.constraint == self.__constraint).all())
        candidates.union(set(x[0] for x in
                             self.__s.query(distinct(ActivitySimilarity.activity_journal_lo_id)).
                             filter(ActivitySimilarity.constraint == self.__constraint).all()))
        candidates = sorted(candidates)
        # shuffle(candidates)  # skip for repeatability
        return super().run(candidates)

    def neighbourhood(self, candidate, epsilon):
        qlo = self.__s.query(ActivitySimilarity.activity_journal_lo_id). \
            filter(ActivitySimilarity.constraint == self.__constraint,
                   ActivitySimilarity.activity_journal_hi_id == candidate,
                   (self.__max_similarity - ActivitySimilarity.similarity) / self.__max_similarity < epsilon)
        qhi = self.__s.query(ActivitySimilarity.activity_journal_hi_id). \
            filter(ActivitySimilarity.constraint == self.__constraint,
                   ActivitySimilarity.activity_journal_lo_id == candidate,
                   (self.__max_similarity - ActivitySimilarity.similarity) / self.__max_similarity < epsilon)
        return [x[0] for x in qlo.all()] + [x[0] for x in qhi.all()]


class NearbyStatistics(SimilarityStatistics):

    def run(self):
        super().run()
        with self._db.session_context() as s:
            latest_groups = self._latest_timestamp(s, NearbyStatistics)
            latest_similarity = self._latest_timestamp(s, SimilarityStatistics)
            if not latest_groups or latest_similarity.time > latest_groups.time:
                with Timestamp(owner=NearbyStatistics, constraint=self._config.constraint).on_success(self._log, s):
                    d_min, n = expand_max(self._log, 0, 1, 5, lambda d: len(self.dbscan(s, d)))
                    self._log.info('%d groups at d=%f' % (n, d_min))
                    self.save(s, self.dbscan(s, d_min))

    def dbscan(self, s, d):
        return NearbySimilarityDBSCAN(self._log, s, self._config.constraint, d, 3).run()

    def save(self, s, groups):
        s.query(ActivityNearby). \
            filter(ActivityNearby.constraint == self._config.constraint).delete()
        for i, group in enumerate(groups):
            self._log.info('Group %d has %d members' % (i, len(group)))
            for activity_journal_id in group:
                s.add(ActivityNearby(constraint=self._config.constraint, group=i,
                                     activity_journal_id=activity_journal_id))


class NearbyCalculator(UniProcCalculator):

    def __init__(self, *args, constraint=None, **kargs):
        self.constraint = self._assert('constraint', constraint)
        super().__init__(*args, **kargs)

    def _missing(self, s):
        latest_similarity = Timestamp.get(s, self.owner_in, self.constraint)
        latest_groups = Timestamp.get(s, self.owner_out, self.constraint)
        if not latest_groups or latest_similarity.time > latest_groups.time:
            return [True]
        else:
            return []

    def _delete(self, s):
        Timestamp.clear(s, self.owner_out, constraint=self.constraint)
        s.query(ActivityNearby).filter(ActivityNearby.constraint == self.constraint).delete()

    def _run_one(self, s, missed):
        with Timestamp(owner=NearbyStatistics, constraint=self.constraint).on_success(log, s):
            d_min, n = expand_max(log, 0, 1, 5, lambda d: len(self.dbscan(s, d)))
            log.info(f'{n} groups at d={d_min}')
            self.save(s, self.dbscan(s, d_min))

    def dbscan(self, s, d):
        return NearbySimilarityDBSCAN(log, s, self.constraint, d, 3).run()

    def save(self, s, groups):
        for i, group in enumerate(groups):
            log.info(f'Group {i} has {len(group)} members')
            for activity_journal_id in group:
                s.add(ActivityNearby(constraint=self.constraint, group=i,
                                     activity_journal_id=activity_journal_id))
