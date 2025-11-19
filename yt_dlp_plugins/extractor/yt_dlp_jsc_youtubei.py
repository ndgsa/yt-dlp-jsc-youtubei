from yt_dlp.extractor.youtube.jsc.provider import (
    register_provider,
    register_preference,
    JsChallengeProvider,
    JsChallengeRequest,
    JsChallengeResponse,
    JsChallengeProviderError,
    JsChallengeProviderRejectedRequest,
    JsChallengeType,
    JsChallengeProviderResponse,
    NChallengeOutput,
    SigChallengeOutput,
)
from yt_dlp.extractor.youtube.jsc._builtin.ejs import EJSBaseJCP
from yt_dlp.utils import is_outdated_version, traverse_obj, Popen
from yt_dlp.utils._jsruntime import JsRuntimeInfo
from yt_dlp.globals import supported_js_runtimes

import os
import shutil
import subprocess
import json
import re
import typing
import collections
import functools


@register_provider
class YoutubeiJCP(JsChallengeProvider):
    PROVIDER_VERSION = "0.0.1"
    PROVIDER_NAME = "yt-dlp-jsc-youtubei"
    BUG_REPORT_LOCATION = 'https://github.com/ndgsa/yt-dlp-jsc-youtubei'

    SUPPORTED_RUNTIMES = ['deno', 'node']
    # node < 18 throws error on extracting decryption function
    # deno < 1.22 throws error on extracting decryption function
    SUPPORTED_RUNTIMES_MIN_SUPPORTED_VERSION = {'node': (18, 0, 0), 'deno': (1, 22, 1)}
    JS_RUNTIME_NAME = None
    JS_RUNTIME_VERSION = None
    JS_RUNTIME = None

    _SUPPORTED_TYPES = [JsChallengeType.N, JsChallengeType.SIG]

    youtubei_version = '16.0.1'

    USER_HOME = os.path.expanduser('~')
    js_cachedir = os.path.join(USER_HOME, '.cache', 'yt-dlp', 'yt-dlp-jsc-youtubei')

    # node = self.ie._jsc_director.providers.get('Node') # ._run_js_runtime("console.log('test')") # not working

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._available = True

    def find_executable(self, executable_name):
        """Search for an executable in the system path"""
        # print(os.path.realpath(shutil.which('node', mode=os.F_OK | os.X_OK, path=os.environ["PATH"])))
        executable_path_list = []
        if os.name == "nt": executable_name = executable_name + '.EXE'
        for directory in os.get_exec_path():
            executable_path = os.path.join(directory, executable_name)
            if os.path.isfile(executable_path) and os.access(executable_path, os.X_OK):
               executable_path_list.append(executable_path)
        if len(executable_path_list) == 0: return [] # none
        executable_path_list.reverse()
        return executable_path_list #[-1] # if multiple occourences

    def find_executable1(self, executable_name):
        """Search for an executable"""
        output = None
        for cmd in ['where', 'which']:
            try: output = subprocess.run([cmd, executable_name], stdout=subprocess.PIPE, text=True, check=True)
            except FileNotFoundError: continue
            else:
                if output.returncode == 0: return output.stdout.split('\n')[:-1] # if multiple occourences
                else: output = []
        return output

    def _get_js_runtime(self):
        for runtime_name in self.SUPPORTED_RUNTIMES:
            for runtime_path in self.find_executable(runtime_name):
                # runtime_path = self.find_executable(runtime_name)
                runtime = supported_js_runtimes.value.get(runtime_name)(path=runtime_path)
                m_s_v = runtime.MIN_SUPPORTED_VERSION
                runtime.MIN_SUPPORTED_VERSION = self.SUPPORTED_RUNTIMES_MIN_SUPPORTED_VERSION[runtime_name]
                #runtime.name, runtime.path, runtime.version, runtime.version_tuple, runtime.supported,
                # print(is_outdated_version('0.0.0', '57.56.100', False))
                if runtime and runtime.info and runtime.info.supported and shutil.which(runtime.info.path):
                    self.JS_RUNTIME_NAME = runtime.info.name
                    self.JS_RUNTIME_VERSION = runtime.info.version
                    runtime.MIN_SUPPORTED_VERSION = m_s_v # restore default
                    self.JS_RUNTIME = runtime.info.path
                    return self.JS_RUNTIME
                runtime.MIN_SUPPORTED_VERSION = m_s_v # restore default
        return None

    @functools.lru_cache(maxsize=1)
    def is_available(self):
        if self.JS_RUNTIME is None:
            self.JS_RUNTIME = self._get_js_runtime()
        if self.JS_RUNTIME:
            return self._available
        else:
            self.logger.debug('No js runtime is found. Install supported runtime [ node, deno, bun ]')
            return False

    def _run_js_runtime(self, js_file, *args):
        if self.JS_RUNTIME_NAME == 'deno': cmd = [self.JS_RUNTIME, 'run', '--allow-run', '--allow-net', js_file, *args,]
        elif self.JS_RUNTIME_NAME == 'node': cmd = [self.JS_RUNTIME, js_file, *args,]
        else: raise JsChallengeProviderError('No JS runtime is found.')
        self.logger.debug(f'Running {self.JS_RUNTIME_NAME} with args: {args}')
        with Popen(cmd, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            stdout, stderr = proc.communicate_or_kill()
            if proc.returncode or stderr:
                msg = f'Error running {self.JS_RUNTIME_NAME} process (returncode: {proc.returncode})'
                if stderr:
                    msg = f'{msg}: {stderr.strip()[:400]}'
                raise JsChallengeProviderError(msg)
        return (stdout, stderr, proc.returncode)

    @functools.lru_cache(maxsize=1)
    def _get_youtubei(self, youtubei_version, use_js_runtime=False):
        '''Download youtubei.js library if not exists.'''
        youtubei_url = f"https://cdn.jsdelivr.net/npm/youtubei.js@{youtubei_version}/bundle/browser.min.js"
        if use_js_runtime: youtubei_name = 'youtubei_' + youtubei_version.replace('.', '') + '.mjs'
        else: youtubei_name = 'youtubei.min.js'
        youtubei_file = os.path.join(self.js_cachedir, youtubei_name)
        # youtubei_file = os.path.join(os.path.dirname(__file__), youtubei_name)

        additional_js_code = '''(async function () {let args; let innertube; let player_version; if (typeof Deno !== 'undefined'){args = Deno.args;} else if (typeof Bun !== 'undefined'){args = Bun.args;} else {args = process.argv.slice(2);}; try{if (args.length === 0){innertube = await Innertube.create({'client': 'TV', 'lang': 'en'}); player_version = innertube.session.player.player_id;} else {player_version = args[0]; innertube = await Innertube.create({'client': 'TV', 'lang': 'en', 'retrieve_player': 'true', 'player_id': player_version});};} catch (innertube_error){console.error(innertube_error);return;}; const session_info = {}; session_info.data = innertube.session.player.data; session_info.player_version = player_version; console.log(JSON.stringify(session_info));})();'''

        if not os.path.isdir(self.js_cachedir):
            self.logger.debug(f'Creating {self.js_cachedir} directory')
            os.makedirs(self.js_cachedir)

        if not os.path.isfile(youtubei_file):
            content = self.ie._download_webpage(youtubei_url, None, note=f'Downloading youtubei.js library from url "{youtubei_url}"')
            if content:
                with open(youtubei_file, 'w', encoding='utf-8') as file:
                    self.logger.debug(f'Saving youtubei.js library to "{youtubei_file}"')
                    file.write(f"{content}\n{additional_js_code}")
            else:
                raise JsChallengeProviderError(f'Unable to download youtubei.js library')

        if not os.path.isfile(youtubei_file):
            self._available = False
            raise JsChallengeProviderError(f'{youtubei_name} library not available')
        else:
            self.logger.debug(f'Using {youtubei_name} library')

        return youtubei_file

    def _extract_decryption_function(self, player_version):
        decryption_function = None
        decrypt_function_file = os.path.join(self.js_cachedir, f'''signature_func_{player_version}.js''')

        additional_js_code = '''(function () {let args; let challenges = []; let result = {}; result['type']='result'; result['responses'] = []; if (typeof Deno !== 'undefined'){args = Deno.args;} else if (typeof Bun !== 'undefined'){args = Bun.args;} else {args = process.argv.slice(2);}; if (args.length === 1){challenges = JSON.parse(args[0]);} else {console.log(JSON.stringify(result)); return;}; try{for(const challenge of challenges){let challenge_result = {}; if (challenge['type'] === 'n'){for(const n_c of challenge['challenges']){challenge_result[n_c]=exportedVars.nFunction(n_c);};} else if (challenge['type'] === 'sig'){for(const s_c of challenge['challenges']){challenge_result[s_c]=exportedVars.sigFunction(s_c);};}; result['responses'].push({'type':'result','data':challenge_result});};} catch (decryption_error){console.error(decryption_error);return;}; console.log(JSON.stringify(result));})();'''

        if not os.path.isfile(decrypt_function_file):
            youtubei_file = self._get_youtubei(self.youtubei_version, use_js_runtime=True)
            self.logger.debug(f'Extracting decryption function for player {player_version} using {os.path.basename(youtubei_file)}')
            stdout, stderr, proc_returncode = self._run_js_runtime(youtubei_file, player_version)
            try: output = json.loads(stdout)
            except json.decoder.JSONDecodeError as e: raise JsChallengeProviderError('Unable to extract decryption function')
            if output.get('data', {}).get('output'):
                with open(decrypt_function_file, 'w') as file:
                    self.logger.debug(f'Saving decryption function to {os.path.basename(decrypt_function_file)}')
                    content = output.get('data', {}).get('output')
                    file.write(f"{content}\n{additional_js_code}")

        if not os.path.isfile(decrypt_function_file):
            raise JsChallengeProviderError(f'Unable to load signature function')
        else:
            self.logger.debug(f'Using decryption function from cache {os.path.basename(decrypt_function_file)}')

        return decrypt_function_file

    def _extract_challenges(self, requests: list[JsChallengeRequest]):
        player_version = ''
        player_version_re = re.compile(r'\/s\/player\/([a-fA-F0-9]+)?\/[\w\S]+?\.js')
        json_requests = []
        for request in requests:
            player_version_match = re.search(player_version_re, request.input.player_url)
            if player_version_match:
                player_version_ = player_version_match.group(1)
                if player_version != '' and player_version != player_version_: self.logger.error(f'player_version are different')
                player_version = player_version_
            else: raise JsChallengeProviderError('Unable to extract player_version')
            json_requests.append({
                'type': request.type.value,
                'challenges': request.input.challenges,
                'video_id': request.video_id,
                'player_version': player_version,
            })
        return json_requests, player_version

    def _is_valid_challenge_response(self, stdout):
        try:
            output = json.loads(stdout)
        except json.decoder.JSONDecodeError as e:
            raise JsChallengeProviderError('Unable to decode JSON response')
        responses = output.get('responses')
        for response in responses:
            if set(response.keys()) == set(['type', 'data']):
                for e,d in response['data'].items():
                    # print(len(e), len(d))
                    if len(d) not in [14, 100, 104]:
                        self.logger.info(f'Ciphers length encrypted:{len(e)} decrypted:{len(d)}')
                        self.logger.error(f'Unexpected length of decrypted n/sig {d}')
            else:
                self.logger.error(f'Invalid challenge decryption result {response}')
        return responses

    def _real_bulk_solve(self, requests: list[JsChallengeRequest]) -> typing.Generator[JsChallengeProviderResponse, None, None]:
        self.logger.info(f"Solving JS challenges using {self.JS_RUNTIME_NAME} v{self.JS_RUNTIME_VERSION}")
        self.logger.debug(f'Got {len(requests)} challenges to solve.')

        grouped: dict[str, list[JsChallengeRequest]] = collections.defaultdict(list)
        for request in requests:
            if len(request.input.challenges[0]) >= 255:
                raise JsChallengeProviderRejectedRequest('Challenges longer than 255 is not supported', expected=True)
            grouped[request.video_id].append(request) # group by video_id

        for video_id, grouped_requests in grouped.items():
            json_requests, player_version = self._extract_challenges(grouped_requests)
            self.logger.debug(f'Using player { player_version }')
            decrypt_function_file = self._extract_decryption_function(player_version)
            self.logger.debug(f'Executing {self.JS_RUNTIME_NAME} command to decrypt challenges for video_id {video_id}.')
            json_requests = json.dumps(json_requests, separators=(',', ':'), indent=None) # crap
            stdout, stderr, proc_returncode = self._run_js_runtime(decrypt_function_file, json_requests)
            responses = self._is_valid_challenge_response(stdout)
            self.logger.debug(f'Decrypted challenges: {responses}')
            for request, response_data in zip(grouped_requests, responses, strict=True):
                if response_data['type'] == 'error':
                    yield JsChallengeProviderResponse(request, None, response_data['error'])
                else:
                    yield JsChallengeProviderResponse(request, JsChallengeResponse(request.type, (
                        NChallengeOutput(response_data['data']) if request.type is JsChallengeType.N
                        else SigChallengeOutput(response_data['data']))))


@register_preference(YoutubeiJCP)
def youtubei_provider_preference(provider: JsChallengeProvider, requests: list[JsChallengeRequest]) -> int:
    return 50

