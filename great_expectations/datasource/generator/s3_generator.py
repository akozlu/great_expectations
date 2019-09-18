import re

import logging

try:
    import boto3
except ImportError:
    boto3 = None

logger = logging.getLogger(__name__)

from great_expectations.datasource.generator.batch_generator import BatchGenerator
from great_expectations.exceptions import BatchKwargsError


class S3Generator(BatchGenerator):
    def __init__(self,
                 name="default",
                 datasource=None,
                 bucket=None,
                 reader_options=None,
                 assets=None,
                 delimiter="/",
                 max_keys=1000):
        super(S3Generator, self).__init__(name, type_="s3", datasource=datasource)
        if reader_options is None:
            reader_options = {}

        if assets is None:
            assets = {
                "default": {
                    "prefix": "",
                    "glob": "*",
                    "partition_regex": "(.*)"
                }
            }

        self._bucket = bucket
        self._reader_options = reader_options
        self._assets = assets
        self._delimiter = delimiter
        self._max_keys = max_keys
        self._iterators = {}
        try:
            self._s3 = boto3.client('s3')
        except TypeError:
            raise(ImportError("Unable to load boto3, which is required for S3 generator"))

    @property
    def reader_options(self):
        return self._reader_options

    @property
    def asset_globs(self):
        return self._assets

    @property
    def bucket(self):
        return self._bucket

    def get_available_data_asset_names(self):
        return set(self._assets.keys())

    def _get_iterator(self, generator_asset, **kwargs):
        logger.debug("Beginning S3Generator _get_iterator for generator_asset: %s" % generator_asset)

        if generator_asset not in self._assets:
            batch_kwargs = {
                "generator_asset": generator_asset,
            }
            batch_kwargs.update(kwargs)
            raise BatchKwargsError("Unknown asset_name %s" % generator_asset, batch_kwargs)

        if generator_asset not in self._iterators:
            self._iterators[generator_asset] = {}

        asset_config = self._assets[generator_asset]

        return self._build_asset_iterator(
            asset_config=asset_config,
            iterator_dict=self._iterators[generator_asset]
        )

    def _build_batch_kwargs_path_iter(self, path_list):
        for path in path_list:
            yield self._build_batch_kwargs(path)

    def _build_batch_kwargs(self, key, asset_reader_options=None):
        batch_kwargs = {
            "s3": "s3a://" + self.bucket + "/" + key,
        }
        batch_kwargs.update(self.reader_options)
        if asset_reader_options is not None:
            batch_kwargs.update(asset_reader_options)
        return batch_kwargs

    def _build_asset_iterator(self, asset_config, iterator_dict):
        query_options = {
            "Bucket": self.bucket,
            "Delimiter": asset_config.get("delimiter", self._delimiter),
            "Prefix": asset_config.get("prefix", None),
            "MaxKeys": asset_config.get("max_keys", self._max_keys)
        }

        if "continuation_token" in iterator_dict:
            query_options.update({
                "ContinuationToken": iterator_dict["continuation_token"]
            })

        logger.debug("Fetching objects from S3 with query options: %s" % str(query_options))
        asset_options = self._s3.list_objects_v2(**query_options)
        if "Contents" not in asset_options:
            raise BatchKwargsError(
                "Unable to build batch_kwargs. The asset may not be configured correctly. If s3 returned common "
                "prefixes it may not have been able to identify desired keys, and they are included in the "
                "incomplete batch_kwargs object returned with this error.",
                {
                    "asset_configuration": asset_config,
                    "common_prefixes": asset_options["CommonPrefixes"] if "CommonPrefixes" in asset_options else None
                }
            )

        keys = [item["Key"] for item in asset_options["Contents"] if item["Size"] > 0]
        keys = [key for key in filter(lambda x: re.match(asset_config.get("regex_filter", ".*"), x) is not None, keys)]
        for key in keys:
            yield self._build_batch_kwargs(
                key,
                asset_config.get("reader_options", {})
            )

        if asset_options["IsTruncated"]:
            iterator_dict["continuation_token"] = asset_options["NextContinuationToken"]
            # Recursively fetch more
            for batch_kwargs in self._build_asset_iterator(asset_config, iterator_dict):
                yield batch_kwargs