"""Release integrity and remote-state checks without changing a real Space."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('hf_space', Path(__file__).resolve().parents[1] / 'tools/hf_space.py')
hf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hf)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = {'repoId':'owner/space', 'origin':'https://owner-space.hf.space', 'branch':'main'}
        self.bundle = self.root / 'build/huggingface/bundle-test'
        self.web = self.bundle / 'public/workbench'
        self.web.mkdir(parents=True)
        (self.web / 'index.html').write_text('<html>sfx-workbench-config</html>')
        files = hf.inventory(self.web)
        self.version = {'contentDigest':hf.digest(json.dumps(files, sort_keys=True).encode()), 'publicationId':'fixture'}
        hf.write(self.web / 'package-version.json', self.version)
        self.receipt = self.root / 'receipt.json'
        self.record = {'artifact':self.bundle.relative_to(self.root).as_posix(),
                       'space':self.settings, 'packageVersion':self.version, 'files':hf.inventory(self.bundle)}
        hf.write(self.receipt, self.record)
        self.patches = [patch.object(hf, 'ROOT', self.root), patch.object(hf, 'config', return_value=self.settings)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def api(self, private=True, commit='old', running='old'):
        api = Mock()
        api.space_info.return_value = SimpleNamespace(id='owner/space', private=private, sha=commit,
            runtime=SimpleNamespace(stage='RUNNING', raw={'sha':running}), card_data={'fullWidth':True,'header':'mini'})
        return api

    def test_changed_asset_is_not_deployable(self):
        hf.checked_artifact(self.receipt)
        (self.web / 'index.html').write_text('modified after packaging')
        with self.assertRaisesRegex(ValueError, 'ARTIFACT_CHANGED'):
            hf.checked_artifact(self.receipt)

    def test_unlisted_credentials_are_refused(self):
        (self.bundle / '.env').write_text('TEST_ONLY=not-a-secret')
        with self.assertRaisesRegex(ValueError, 'PACKAGE_FILE_REFUSED'):
            hf.checked_artifact(self.receipt)

    def test_public_space_is_refused_before_upload(self):
        api = self.api(private=False)
        with self.assertRaisesRegex(ValueError, 'PRIVATE_SPACE_REQUIRED'):
            hf.upload(api, self.bundle, self.record, 'test')
        api.create_commit.assert_not_called()

    def test_upload_pins_parent_and_only_removes_obsolete_workbench_assets(self):
        from huggingface_hub import CommitOperationDelete
        api = self.api()
        api.list_repo_files.return_value = ['public/workbench/removed.js','notes/keep.md','public/workbench/index.html']
        api.create_commit.return_value = SimpleNamespace(oid='new')
        result = hf.upload(api, self.bundle, self.record, 'test')
        args = api.create_commit.call_args.kwargs
        self.assertEqual(args['parent_commit'], 'old')
        self.assertEqual(args['revision'], 'main')
        self.assertEqual([op.path_in_repo for op in args['operations'] if isinstance(op, CommitOperationDelete)],
                         ['public/workbench/removed.js'])
        self.assertEqual(result['commit'], 'new')
        self.assertFalse(result['verified'])

    def test_previous_running_container_cannot_verify_new_release(self):
        release = {'space':self.settings, 'commit':'new', 'packageVersion':self.version}
        with self.assertRaisesRegex(ValueError, 'SPACE_BUILD_TIMEOUT'):
            hf.verify(self.api(commit='new', running='old'), release, 'unused', 0)

    def test_concurrent_release_is_not_reported_as_ours(self):
        release = {'space':self.settings, 'commit':'new', 'packageVersion':self.version}
        with self.assertRaisesRegex(ValueError, 'SPACE_REVISION_CHANGED'):
            hf.verify(self.api(commit='someone-else', running='someone-else'), release, 'unused', 0)


if __name__ == '__main__':
    unittest.main()
