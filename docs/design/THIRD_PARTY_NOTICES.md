# THIRD_PARTY_NOTICES

`pyproject.toml`에 선언된 의존성 중 자체 재배포 고지 의무가 있는 항목의 원문.
근거: `docs/design/INDICATOR_OSS_EVAL.md` §2·§5·§6.

## pandas-ta-classic (MIT)

`pandas-ta-classic` 0.6.52+ (`xgboosted/pandas-ta-classic`), IND-11(§9.9)에서
채택. 의존성 선언: `pyproject.toml` `pandas-ta-classic>=0.6.52`. `oracle`
extra(`tulipy`, LGPL-3.0)는 설치하지 않는다.

배포물 `LICENSE`(2026-09-23 wheel `pandas_ta_classic-0.8.32.dist-info/licenses/
LICENSE`에서 원문 확인):

```
The MIT License (MIT)

Copyright (c) 2021+ pandas-ta contributors
Copyright (c) 2024+ pandas-ta-classic contributors (xgboosted/pandas-ta-classic)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
