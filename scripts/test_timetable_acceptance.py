"""Timetable and intake acceptance using isolated, synthetic input only."""
import csv
import io
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Back_end.timetable_import import parse_timetable_csv, import_timetable
from Back_end.city_service import import_city_service, resolve_headways
from Back_end.data_intake import map_fields, inspect_package
from Back_end.custom_inputs import build_template_package


class TimetableAcceptanceTests(unittest.TestCase):
    def test_discrete_boundaries_duplicates_and_pending(self):
        result = parse_timetable_csv('route_id,departure_times\na,07:00;07:10;07:10;08:50;09:00;17:00\n')
        row = result['rows'][0]
        self.assertEqual(row['am_departure_count'], 3)
        self.assertEqual(row['am_headway_min'], 55)
        self.assertEqual(row['flat_headway_min'], '')
        self.assertEqual(row['pm_headway_min'], '')
        self.assertEqual(result['summary']['duplicate_departures'], 1)
        self.assertEqual(result['summary']['pending_cells'], 2)

    def test_fixed_intervals_weight_by_duration(self):
        row = parse_timetable_csv('route_id,start_time,end_time,interval_min\na,07:00,08:00,10\na,08:00,09:00,20\n')['rows'][0]
        self.assertAlmostEqual(row['am_headway_min'], 120 / 9)
        self.assertEqual(row['am_headway_source'], 'timetable_fixed')

    def test_overlap_is_pending_not_average(self):
        row = parse_timetable_csv('route_id,start_time,end_time,interval_min\na,07:00,08:30,10\na,08:00,09:00,20\n')['rows'][0]
        self.assertEqual(row['am_headway_min'], '')
        self.assertIn('重叠', row['am_pending_reason'])

    def test_ambiguous_interval_and_invalid_clock_stay_pending(self):
        for start, interval in (('07:00', '5-10'), ('24:00', '10'), ('07:00', '0')):
            with self.subTest(start=start, interval=interval):
                result = parse_timetable_csv(f'route_id,start_time,end_time,interval_min\na,{start},09:00,{interval}\n')
                self.assertEqual(result['summary']['pending_cells'], 3)
                self.assertEqual(result['rows'][0]['am_headway_min'], '')

    def test_weekend_only_does_not_become_weekday(self):
        import json
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['route_id', 'timedesc_json'])
        writer.writerow(['a', json.dumps({'rule_group': [{'date': {'day_type': ['weekend']}, 'time_group': [{'start_time': '07:00', 'end_time': '09:00', 'interval_time': '10'}]}]})])
        weekday = parse_timetable_csv(buffer.getvalue())
        weekend = parse_timetable_csv(buffer.getvalue(), day_type='weekend')
        self.assertEqual(weekday['rows'][0]['am_headway_min'], '')
        self.assertEqual(weekend['rows'][0]['am_headway_min'], 10)

    def test_malformed_schema_and_empty_csv_rejected(self):
        for content in ('route_id\na\n', 'route_id,departure_time\n', 'departure_time\n07:00\n'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_timetable_csv(content)

    def fixture(self, root):
        folder = root / 'AcceptOffline/network'
        folder.mkdir(parents=True)
        (folder / 'bus_headway_template.csv').write_text('route_id,route_name,topology_status\na,A,ready\nb,B,ready\n')
        return {'id': 'AcceptOffline', 'name': 'Fixture'}

    def test_import_separate_periods_and_explicit_default(self):
        with TemporaryDirectory() as temp, patch('Back_end.city_service.cached_city_network', return_value={'signature': 'fixture'}):
            root = Path(temp)
            city = self.fixture(root)
            result = import_timetable(city, root, root, 'route_id,departure_times\na,07:00;07:10;17:00;17:20\n')
            self.assertEqual(result['provenance']['kind'], 'timetable')
            for period, expected in (('am_peak', 10), ('pm_peak', 20), ('offpeak', 15)):
                path, summary = resolve_headways(city['id'], root, root / period, 15, period)
                rows = list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
                self.assertEqual(float(rows[0]['selected_headway_min']), expected)
                self.assertEqual(summary['headway_default_share'], 1 if period == 'offpeak' else .5)

    def test_all_pending_timetable_is_preserved(self):
        with TemporaryDirectory() as temp, patch('Back_end.city_service.cached_city_network', return_value={'signature': 'fixture'}):
            root = Path(temp)
            city = self.fixture(root)
            result = import_timetable(city, root, root, 'route_id,departure_time\na,07:00\n')
            self.assertEqual(result['am_headway_routes'], 0)
            saved = root / city['id'] / 'service/bus_headways_by_period.csv'
            row = list(csv.DictReader(io.StringIO(saved.read_text(encoding='utf-8-sig'))))[0]
            self.assertEqual(row['am_headway_min'], '')
            self.assertTrue(row['am_pending_reason'])

    def test_service_conflicts_rejected_without_writes(self):
        cases = [
            'route_id,peak_headway_min,flat_headway_min,peak_frequency_per_hour\na,10,20,3\n',
            'route_id,peak_headway_min,flat_headway_min\na,10,20\na,10,20\n',
            'route_id,peak_headway_min,flat_headway_min\nunknown,10,20\n',
            'route_id,peak_frequency_per_hour,flat_frequency_per_hour\na,1e-320,3\n',
        ]
        with TemporaryDirectory() as temp, patch('Back_end.city_service.cached_city_network', return_value={'signature': 'fixture'}):
            root = Path(temp)
            city = self.fixture(root)
            for content in cases:
                with self.subTest(content=content), self.assertRaises(ValueError):
                    import_city_service(city, root, root, content)
                self.assertFalse((root / city['id'] / 'service').exists())

    def test_mapping_conflicts_and_missing_sources(self):
        frame = pd.DataFrame({'id': ['a'], 'source': ['b']})
        for mapping in ({'id': 'missing'}, {'id': 'source', 'name': 'source'}, {'id': 'source'}, {'unknown': 'source'}):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                map_fields(frame, 'poi', {'poi': mapping})
        self.assertEqual(list(frame.columns), ['id', 'source'])

    def test_inspection_is_readonly_and_reports_quality(self):
        result = inspect_package(build_template_package(), coordinate_system='WGS84')
        self.assertEqual(result['quality']['layers']['poi']['rows'], 2)
        self.assertEqual(result['quality']['broken_segments'], 0)
        with self.assertRaises(ValueError):
            inspect_package(b'not zip')


if __name__ == '__main__':
    unittest.main()
