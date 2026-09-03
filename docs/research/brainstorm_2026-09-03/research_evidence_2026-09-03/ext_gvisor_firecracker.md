# gVisor / Firecracker 코드 레벨 분석 — Enterprise Trading-OS 설계 스터디

- 분석 대상:
  - `C:/Users/aiaa1/AppData/Local/Temp/claude/.../scratchpad/ext2/gvisor` (Repo: https://github.com/google/gvisor, `git clone --depth 1`)
  - `C:/Users/aiaa1/AppData/Local/Temp/claude/.../scratchpad/ext2/firecracker` (Repo: https://github.com/firecracker-microvm/firecracker, `git clone --depth 1`)
- License: 둘 다 **Apache License 2.0** — gVisor `LICENSE`(표준 Apache-2.0 전문), Firecracker `LICENSE`(동일) + `NOTICE`. Firecracker의 `SPECIFICATION.md`/`swagger/firecracker.yaml`에도 `license: { name: "Apache 2.0" }` 명시.
- 최신 커밋(shallow clone 시점, `git log -1`):
  - gVisor: `80bb741691be65cedb6688ac518bef3664af0fcc`, **2026-09-01**, "Close exposed race window in stub ThreadID setting."
  - Firecracker: `9384f395f5d47f05b68a657525e209f8aa9c8264`, **2026-09-02**, "docs(changelog): note the virtio-mem block state fix."
  - 두 프로젝트 모두 조회 시점 하루~이틀 전 커밋이 있는 활발히 유지보수되는 프로젝트.
- 배경: AIOS는 현재 전략을 선언적 condition 문자열로 자체 인터프리터가 평가하는 구조라 "사용자 제출 임의 코드 실행" 표면이 전혀 없다(`safe by construction`, prior audit 결론). 그러나 `AIOS_Target_Architecture_Freeze_v0.1_2026-09-03.md:31,147-148`은 Execution Plane 하위에 **"Sandbox Tier Manager(gVisor/Firecracker/WASM)"**를 이름만 배치하고 "PoC 전까지 세부 설계 보류"라고 명시해 두었다. 이 문서는 그 PoC 이전 단계에서 gVisor/Firecracker가 코드 레벨로 무엇을 제공하는지, AIOS의 실제 니즈(마켓플레이스 전략 코드 격리)에 맞는지를 검증한다.

---

## 1. gVisor의 격리 메커니즘 — Sentry, 배포 단위

gVisor의 핵심은 **Sentry**라는 유저스페이스 "application kernel"이다. `README.md`가 이를 명확히 정의한다.

`gvisor/README.md:9-13`
```
**gVisor** provides a strong layer of isolation between running applications and
the host operating system. It is an application kernel that implements a
[Linux-like interface][linux]. Unlike Linux, it is written in a memory-safe
language (Go) and runs in userspace.
```

gVisor는 스스로 "seccomp-bpf 같은 syscall 필터도 아니고, VirtualBox/QEMU 같은 VM도 아닌 제3의 접근"이라고 못박는다(`README.md:22-27`: "gVisor is **not a syscall filter**... gVisor is also **not a VM**... **gVisor takes a distinct third approach**"). 구현은 `pkg/sentry/`(Go, ~수십 개 하위 패키지: `kernel`, `fsimpl`, `mm`, `platform` 등) 아래에 있고, 애플리케이션의 syscall을 가로채는 방식은 `pkg/sentry/platform/` 아래 pluggable "Platform" 추상화로 분리되어 있다.

`g3doc/architecture_guide/platforms.md:5-24`
```
gVisor requires a platform to implement interception of syscalls, basic context
switching, and memory mapping functionality. Internally, gVisor uses an
abstraction sensibly called [`Platform`][platform]. A simplified version of this
interface looks like:

type Platform interface {
    NewAddressSpace() (AddressSpace, error)
    NewContext() Context
}
```

두 플랫폼이 현역이다(`pkg/sentry/platform/{ptrace,systrap,kvm}`):

- **ptrace** — `PTRACE_SYSEMU`로 유저 코드를 실행하되 호스트 syscall을 절대 완료시키지 않음(`platforms.md:65-67`: "uses [`PTRACE_SYSEMU`][ptrace] to execute user code without allowing it to execute host system calls"). 가상화 없이 어디서나 동작하지만 컨텍스트 스위치 오버헤드가 가장 크다. **2023년 중반부터 systrap으로 대체되어 더 이상 기본값이 아니고 사실상 deprecated 경로**다(`platforms.md:73-77`: "it is no longer supported and is expected to eventually be removed entirely").
- **systrap**(현재 기본값) — `seccomp`의 `SECCOMP_RET_TRAP`을 이용해 syscall 시 `SIGSYS`를 발생시켜 Sentry가 가로챈다(`platforms.md:52-57`).
- **KVM** — 베어메탈에서 최고 성능, 가상화 확장을 이용해 주소공간 전환/페이지폴트를 가속하지만 sandbox 자체는 여전히 프로세스 모델을 유지한다(`platforms.md:40-45`).

보안 모델 문서는 "ptrace sandbox"라는 오해를 직접 반박한다.

`g3doc/architecture_guide/security.md:243-257`
```
### Is this just a ptrace sandbox?

No: the term "ptrace sandbox" generally refers to software that uses the Linux
ptrace facility to inspect and authorize system calls made by applications,
enforcing a specific policy. These commonly suffer from two issues. First,
vulnerable system calls may be authorized by the sandbox, as the application
still has direct access to some System API. Second, it's impossible to avoid
time-of-check, time-of-use race conditions without disabling multi-threading.

In gVisor, the platforms that use ptrace operate differently. The stubs that are
traced are never allowed to continue execution into the host kernel and complete
a call directly. Instead, all system calls are interpreted and handled by the
Sentry itself...
```

즉 gVisor의 핵심 차별점은 "syscall을 감시/필터링"하는 게 아니라 **syscall을 Sentry가 Go로 재구현해서 대신 처리**하는 것이다(`intro_to_gvisor.md:100-105`: "the gVisor Sentry needs to re-implement Linux in Go... gVisor never passes through any system call to the host").

### 배포 단위 — `runsc`와 OCI 런타임

gVisor는 `runsc`(run-sc, "sandboxed container")라는 OCI 호환 런타임 바이너리로 배포된다.

`README.md:15-19`
```
gVisor includes an [Open Container Initiative (OCI)][oci] runtime called `runsc`
that makes it easy to work with existing container tooling. The `runsc` runtime
integrates with Docker and Kubernetes, making it simple to run sandboxed
containers.
```

Docker 통합은 `runsc install`이 `/etc/docker/daemon.json`에 런타임을 등록하고 `docker run --runtime=runsc ...`로 호출하는 방식(`g3doc/user_guide/quick_start/docker.md:16-37`). Kubernetes 통합은 `runtimeClassName: gvisor`를 pod spec에 지정하면 GKE Sandbox/Minikube/containerd 경유로 동작(`g3doc/user_guide/quick_start/kubernetes.md:6-25`: "it will run pods annotated with `runtimeClassName: gvisor` inside a gVisor sandbox"). `runsc/cmd/create.go`, `start.go`가 OCI lifecycle(`create`/`start`/`delete`)을 구현하고 `runsc/boot/`가 실제 샌드박스 부팅 로직을 담당한다.

**단일 프로세스를 감싸는 "가벼운" 경로도 존재한다.** `runsc do`(`runsc/cmd/do.go`)는 OCI spec 없이 즉석에서 명령 하나를 샌드박스 안에서 실행하는 축소판이다.

`runsc/cmd/do.go:72-77`
```go
// Synopsis implements subcommands.Command.Synopsis.
func (*Do) Synopsis() string {
	return "Simplistic way to execute a command inside the sandbox. It's to be used for testing only."
}
```

문서도 이걸 "testing 전용"으로 명시한다.

`g3doc/architecture_guide/intro_to_gvisor.md:204-231`
```
It can also be used directly for one-off testing, like this:

$ sudo runsc do echo Hello world
Hello world

Note the use of `sudo`, which may give you pause... the sandbox setup process
requires privileges, specifically for setting up the userspace network stack...
For sandboxes that don't require networking, it is possible to run in rootless
mode without sudo:

$ runsc --rootless --network=none do echo Hello world
Hello world
```

같은 문서가 `runsc do`의 한계도 못박는다: "gives the sandbox read-only access to the host's entire filesystem by default... is just a convenience feature to test out gVisor quickly"(`intro_to_gvisor.md:267-269`) — 즉 프로덕션에서 신뢰 경계를 정밀 제어하려면 OCI 컨테이너 경로(Docker/containerd)를 써야 하고, `runsc do`는 "빠른 데모"이지 "프로덕션 서브프로세스 샌드박스 API"가 아니다.

**AIOS 시사점**
- gVisor는 "Python 서브프로세스 하나를 감싸는 라이브러리"가 아니라 **컨테이너 런타임**이다. AIOS Execution Plane이 gVisor를 채택하면 최소 요구사항은 (a) 컨테이너 이미지 빌드 파이프라인, (b) Docker 또는 containerd 데몬 운영, (c) `runsc install`/`runtimeClassName` 설정 — 즉 "전략 코드 하나 실행" 요청마다 컨테이너 라이프사이클(생성→시작→삭제)을 오케스트레이션해야 한다는 뜻이다.
- `runsc do`가 있어 완전한 오버킬은 아니지만, 공식 문서가 "testing only"라고 명시하고 기본적으로 호스트 파일시스템 read-only 마운트라는 느슨한 기본값을 갖는다 — 프로덕션에서 쓰려면 OCI spec을 직접 작성해 마운트/네트워크를 명시적으로 잠가야 하며, 이는 결국 컨테이너 오케스트레이터 경로와 크게 다르지 않은 복잡도로 수렴한다.
- rootless 모드(`--network=none`)가 존재하므로 "sudo 없이 단일 격리 프로세스"는 가능하지만, 네트워크가 필요한 전략(시세 조회 등)이라면 다시 특권 설정이 필요해진다 — AIOS 마켓플레이스 플러그인이 네트워크 I/O를 요구하는 순간 배포 복잡도가 올라간다.

---

## 2. gVisor의 syscall 커버리지/호환성 격차와 성능

### 커버리지

gVisor는 Linux syscall ABI의 부분집합만 구현한다. 공식 호환성 문서가 이를 인정한다.

`g3doc/user_guide/compatibility.md:24-33`
```
While gVisor only implements a subset of the Linux syscall ABI, the
unimplemented part of the ABI is mostly comprised of alternatives to existing
syscalls that gVisor does support. For example, gVisor does not fully support
`io_uring`-related syscalls (as seen below), but does support other I/O-related
syscalls.
```

명시적으로 나열된 갭(`compatibility.md:45-70`):
- in-sandbox cgroups는 *accounting*만 되고 *limit enforcement*는 안 됨(같은 샌드박스 내 경쟁 프로세스 간 리소스 제한 불가).
- `fat32`/`ext3`/`ext4` 같은 블록 디바이스 파일시스템을 샌드박스 커널 내부에서 네이티브 마운트 불가.
- `iptables`는 부분 지원("Docker in gVisor" 시나리오까지만).
- 커스텀 하드웨어 디바이스 파일 미지원(NVIDIA GPU/TPU 예외).
- **`io_uring`은 기본 비활성화, 활성화해도 기본 I/O만 제한 지원.** `nftables`도 유사.
- 샌드박스 *내부*에서 KVM 사용 불가(gVisor 자체가 KVM 플랫폼을 쓰는 것과는 별개).

실제 syscall 테이블(`pkg/sentry/syscalls/linux/linux64.go`, amd64+arm64 두 테이블 합산)을 세어보면:

```
syscalls.Supported(...)          334건
syscalls.PartiallySupported(...) 125건
syscalls.ErrorWithEvent(..., ENOSYS, ...)  38건  (완전 미구현, 호출 시 ENOSYS 반환)
```

예: `io_uring_register`는 아예 미구현이다.

`pkg/sentry/syscalls/linux/linux64.go:378`
```go
427: syscalls.ErrorWithEvent("io_uring_register", linuxerr.ENOSYS, "", nil),
```

`pkg/sentry/unimpl/`은 이런 미구현 syscall 호출을 이벤트로 emit해 관측 가능하게 만드는 인프라만 제공한다(`events.go:33-35`: `EmitUnimplementedEvent`).

### 성능 — CPU-bound 워크로드에는 유리

공식 성능 가이드는 **구조적 비용(structural cost)**과 **구현 비용(implementation cost)**을 구분한다.

`g3doc/architecture_guide/performance.md:109-125`
```
## CPU performance

gVisor does not perform emulation or otherwise interfere with the raw execution
of CPU instructions by the application. Therefore, there is no runtime cost
imposed for CPU operations.
...
This has important consequences for classes of workloads that are often
CPU-bound, such as data processing or machine learning. In these cases, `runsc`
will similarly impose minimal runtime overhead.
```

반면 syscall이 잦은 워크로드는 정반대다.

`performance.md:134-165`
```
Some **structural costs** of gVisor are heavily influenced by the
[platform choice]... For example, `redis` is an application that performs
relatively little work in userspace... We can see that small operations impose
a large overhead, while larger operations, such as `LRANGE`, where more work is
done in the application, have a smaller relative overhead.
```

`ptrace` 플랫폼은 "구조적 비용이 가장 크다"고 문서가 직접 경고한다(`performance.md:58-64`: "the `ptrace` platform... suffers from the highest structural costs by far... users should use Systrap for best performance in most cases"). Systrap/KVM은 이보다 낫지만 여전히 syscall 인터셉션 자체가 구조적 오버헤드다.

**AIOS 관점에서의 워크로드 형태**: "Python 표현식 평가기로 전략 로직을 돌린다"는 시나리오는 CPU-bound(파싱·산술·조건 평가)이고 syscall은 드물다(파일 I/O도 거의 없고, 네트워크는 별도 프록시를 통할 수 있음). 이 형태는 정확히 gVisor가 "구조적 비용이 최소"라고 주장하는 영역과 일치한다 — `sysbench.cpu`/`tensorflow` 벤치마크가 `runsc`와 `runc`(네이티브) 사이에 유의미한 차이가 없음을 보여준다(`performance.md:115-121`).

**AIOS 시사점**
- io_uring/일부 ioctl/블록 디바이스 마운트가 필요한 전략(예: 로컬 대용량 파일 직접 mmap, 커스텀 커널 모듈 연동)은 gVisor에서 아예 동작하지 않거나 성능이 크게 저하될 수 있다 — 그러나 AIOS의 마켓플레이스 전략 코드가 이런 저수준 기능에 의존할 이유가 거의 없으므로(대부분 시세 API 호출 + 산술) 이 갭은 실질적 blocker가 아닐 가능성이 높다.
- 성능 프로파일은 AIOS의 워크로드 형태(CPU-bound, syscall-light)에 유리하게 맞아떨어진다 — "성능 때문에 gVisor를 못 쓴다"는 반론은 근거가 약하다. 오히려 채택을 늦추는 이유는 성능이 아니라 §1에서 지적한 배포/오케스트레이션 복잡도 쪽이다.
- `unimpl` 이벤트 메커니즘을 활용하면 "이 전략 코드가 어떤 미지원 syscall을 호출하려 했는가"를 감사 로그로 남길 수 있어, 향후 마켓플레이스 코드 검증 파이프라인에 관측성 신호로 재사용할 여지가 있다.

---

## 3. Firecracker의 격리 메커니즘 — KVM microVM과 Jailer

Firecracker는 AWS가 Lambda/Fargate를 위해 만든 VMM(Virtual Machine Monitor)으로, KVM 위에서 최소한의 디바이스 모델만 가진 "microVM"을 구동한다.

`README.md:14-20`
```
The main component of Firecracker is a virtual machine monitor (VMM) that uses
the Linux Kernel Virtual Machine (KVM) to create and run microVMs. Firecracker
has a minimalist design. It excludes unnecessary devices and guest-facing
functionality to reduce the memory footprint and attack surface area of each
microVM.
```

디바이스 모델은 극단적으로 축소되어 있다(`FAQ.md:56-63`: "only 6 emulated devices are available: virtio-net, virtio-balloon, virtio-block, virtio-vsock, serial console, and a minimal keyboard controller used only to stop the microVM"). 코드상 `src/vmm/src/devices/{legacy,virtio,pci,acpi,pseudo}`가 이 최소 디바이스 집합을 구현한다.

내부 아키텍처는 프로세스당 정확히 하나의 microVM, API/VMM/vCPU 세 종류 스레드로 구성된다.

`docs/design.md:71-79`
```
Each Firecracker process encapsulates one and only one microVM. The process runs
the following threads: API, VMM and vCPU(s). The API thread is responsible for
Firecracker's API server and associated control plane... In addition to them,
there are one or more vCPU threads (one per guest CPU core). They are created
via KVM and run the `KVM_RUN` main loop.
```

Threat model은 "vCPU 스레드 안의 코드는 시작되는 순간부터 악성으로 취급한다"는 전제로 설계된다(`design.md:83-86`: "all vCPU threads are considered to be running malicious code as soon as they have been started; these malicious threads need to be contained").

### Jailer — seccomp + cgroups + chroot을 VM 기동 *전에* 적용

Jailer(`src/jailer/src/{main.rs,chroot.rs,cgroup.rs,resource_limits.rs,env.rs}`)는 Firecracker 바이너리를 exec하기 *전에* 권한이 필요한 격리 작업을 모두 마치는 별도 바이너리다. 동작 순서가 `docs/jailer.md`에 단계별로 문서화되어 있다.

`docs/jailer.md:117-154` (요약 발췌)
```
- Create the `<chroot_base>/<exec_file_name>/<id>/root` folder... <chroot_dir>
- Copy the file specified with `--exec-file` to `<chroot_dir>/<exec_file_name>`.
  This ensures the new process will not share memory with any other Firecracker
  process.
- Set resource bounds ... by calling `setrlimit()` ...
- Create the cgroup sub-folders... writes the current pid to
  `<cgroup_base>/<parent_cgroup>/<id>/tasks`.
- Call `unshare()` into a new mount namespace, use `pivot_root()` to switch the
  old system root mount point with a new one base in `<chroot_dir>`... and call
  `chroot` into the current directory.
- Use `mknod` to create a `/dev/net/tun` equivalent inside the jail.
- Use `mknod` to create a `/dev/kvm` equivalent inside the jail.
- Drop privileges via setting the provided `uid` and `gid`.
- Exec into `<exec_file_name> --id=<id> --start-time-us=<opaque> ...`
```

즉 chroot/cgroup/네임스페이스 설정 → 권한 drop(`setuid`/`setgid`) → exec 순서로, **Firecracker 프로세스 자신은 이미 언프리빌리지드 상태에서 시작**한다. seccomp은 Jailer가 아니라 Firecracker 프로세스 자신이 스레드별로 설치한다.

`docs/seccomp.md:1-12`
```
Seccomp filters are used by default to limit the host system calls Firecracker
can use. The default filters only allow the bare minimum set of system calls and
parameters that Firecracker needs in order to function correctly.

The filters are loaded in the Firecracker process, on a per-thread basis, as
follows:

- VMM (main) - right before executing guest code on the VCPU threads;
- API - right before launching the HTTP server;
- VCPUs - right before executing guest code.
```

필터는 빌드 타임에 JSON(`resources/seccomp/`)을 `seccompiler-bin`으로 컴파일해 바이너리에 임베드하는 방식(`seccomp.md:19-28`)이고, `--no-seccomp`로 끌 수도 있지만 프로덕션에서는 권장하지 않는다(`seccomp.md:80-86`: "Do **not** use in production").

방어 계층을 문서가 명확히 구분한다.

`docs/design.md:152-165`
```
Firecracker is designed to assure secure isolation using multiple layers. The
first layer of isolation is provided by the Linux KVM and the Firecracker
virtualization boundary. To assure defense in depth, Firecracker should only run
constrained at the process level. This is achieved by the following: seccomp
filters for disallowing unwanted system calls, cgroups and namespaces for
resource isolation, and dropping privileges by jailing the process.
```

Jailer 문서 자체가 신뢰 경계를 명시한다: "the operator invoking the jailer is part of the trusted computing base"(`jailer.md:266-274`) — Jailer는 사람/오케스트레이터가 신뢰할 수 있다는 전제로 동작하고, **격리 대상은 어디까지나 게스트 VM 안의 코드**다.

### 부팅 시간과 밀도 — AWS Lambda 워크로드에 최적화

FAQ가 자주 인용되는 수치를 직접 명시한다.

`FAQ.md:60-64`
```
Firecracker is written in Rust, provides a minimal required device model to the
guest operating system while excluding non-essential functionality...This, along
with a streamlined kernel loading process enables a < 125 ms startup time and a
< 5 MiB memory footprint.
```

Design 문서는 처리량 관점의 수치도 제공한다.

`docs/design.md:26-29`
```
With a microVM configured with a minimal Linux kernel, single-core CPU, and
128 MiB of RAM, Firecracker supports a steady mutation rate of 5 microVMs per
host core per second (e.g., one can create 180 microVMs per second on a host
with 36 physical cores).
```

125ms는 "최소 구성" 기준이며, 실제로는 커널 이미지 로딩, rootfs 준비, API를 통한 순차 설정(boot-source → drives → network-interfaces → actions/InstanceStart) 오버헤드가 추가된다. Getting-started 가이드의 예제 스크립트도 API 호출 후 `sleep 0.015s`(설정 반영 대기), `InstanceStart` 후 `sleep 2s`(부팅+SSH 가용 대기)를 둔다(`docs/getting-started.md:296,313`) — 즉 "125ms"는 하한선이고, 실사용 파이프라인은 초 단위로 보는 것이 안전하다.

**AIOS 관점**: "초당 여러 번" 실행되는 전략 평가(예: 매 tick마다 조건 재평가)에 매번 새 microVM을 띄우는 설계는 125ms~초 단위 지연이 누적되어 부적합하다. Firecracker가 설계된 목표 자체가 "함수 하나를 실행하고 버리는" Lambda 콜드스타트 모델이지, "같은 프로세스가 반복 호출되는" 상시 실행 모델이 아니다. 반대로 "마켓플레이스 플러그인 하나를 온보딩할 때 1회 정적 검증 + 격리 실행"처럼 **저빈도·장수명**에 가까운 시나리오라면 부팅 비용은 상대적으로 덜 부담스럽다.

**AIOS 시사점**
- Jailer의 chroot+cgroup+seccomp 조합은 "운영자가 신뢰된 상태에서 게스트 VM 하나를 완전히 격리"하는 데 최적화되어 있다 — AIOS가 이 정도로 무거운 경계를 매 전략 실행마다 필요로 하는지는 §5에서 재검토해야 한다.
- 125ms(하한)~초 단위(실측) 콜드스타트는 "매 tick 재평가"나 "고빈도 백테스트 반복 호출" 패턴에는 부적합하고, "1회성 신뢰 낮은 코드 검증/샌드박스 실행"(예: 마켓플레이스 신규 플러그인 최초 온보딩 심사) 같은 저빈도 시나리오에만 합리적이다.
- 커널 이미지·rootfs 관리(§4에서 다룰 API의 `boot-source`/`drives` 설정)가 필요하다는 것 자체가 "Python 서브프로세스 하나 돌리기" 대비 훨씬 무거운 운영 표면을 요구한다.

---

## 4. Firecracker의 API 표면 — REST API와 클라이언트 생태계

Firecracker의 제어 평면은 Unix Domain Socket 위의 REST API다. OpenAPI(Swagger 2.0) 스펙이 `src/firecracker/swagger/firecracker.yaml`에 있다.

`src/firecracker/swagger/firecracker.yaml:1-16`
```yaml
swagger: "2.0"
info:
  title: Firecracker API
  description: RESTful public-facing API.
    The API is accessible through HTTP calls on specific URLs
    carrying JSON modeled data.
    The transport medium is a Unix Domain Socket.
  version: 1.17.0-dev
  ...
  license:
    name: "Apache 2.0"
```

주요 엔드포인트(`firecracker.yaml`의 `paths`)는 `/boot-source`, `/drives/{drive_id}`, `/network-interfaces/{iface_id}`, `/machine-config`, `/actions`(InstanceStart 등), `/snapshot/{create,load}`, `/vm`, `/mmds` 등 VM 라이프사이클 전체를 커버한다. 실제 구현은 `src/firecracker/src/api_server/`(요청 파서/핸들러) + `src/vmm/src/vmm_config/`(각 리소스 설정 검증)에 있다.

`docs/getting-started.md`가 보여주는 실제 사용 패턴은 **원시 HTTP 호출을 Unix socket 경유로 순서대로 PUT하는 것**이다.

`docs/getting-started.md:250-311` (요약)
```bash
# Set boot source
sudo curl -X PUT --unix-socket "${API_SOCKET}" \
    --data "{ \"kernel_image_path\": \"${KERNEL}\", \"boot_args\": \"${KERNEL_BOOT_ARGS}\" }" \
    "http://localhost/boot-source"

# Set rootfs
sudo curl -X PUT --unix-socket "${API_SOCKET}" \
    --data "{ \"drive_id\": \"rootfs\", \"path_on_host\": \"${ROOTFS}\", ... }" \
    "http://localhost/drives/rootfs"

# Set network interface
sudo curl -X PUT --unix-socket "${API_SOCKET}" \
    --data "{ \"iface_id\": \"net1\", \"guest_mac\": \"$FC_MAC\", \"host_dev_name\": \"$TAP_DEV\" }" \
    "http://localhost/network-interfaces/net1"

# Start microVM
sudo curl -X PUT --unix-socket "${API_SOCKET}" \
    --data "{ \"action_type\": \"InstanceStart\" }" \
    "http://localhost/actions"
```

이 저장소(firecracker-microvm/firecracker) 안에는 **공식 Python 클라이언트가 없다.** 검색 결과 `docs/` 전체에서 "sdk"를 언급하는 파일은 개발 환경 세팅 문서(`dev-machine-setup.md`) 하나뿐이고, 이는 SDK 클라이언트가 아니라 개발 툴체인 얘기다. AWS/커뮤니티가 별도로 유지하는 `firecracker-go-sdk`(Go)는 존재하지만 이 리포지토리 밖의 별도 프로젝트이며, Python 진영에는 성숙한 공식 바인딩이 없다 — 실무에서는 `requests-unixsocket` 류 어댑터나 순수 `http.client`로 Unix socket 위에 raw HTTP 요청을 구현해야 한다.

**AIOS 시사점**
- Execution Plane이 Firecracker를 구동하려면 "microVM 하나 = Unix socket 하나 + REST 호출 시퀀스(boot-source → drives → network-interfaces → actions)"라는 상태 머신을 직접 오케스트레이션하는 코드를 자체 작성해야 한다. Python 생태계에 성숙한 클라이언트가 없다는 것은 유지보수 부담이 AIOS 쪽으로 전가된다는 뜻이다.
- 커널 이미지/rootfs 파일을 매 microVM마다(또는 Jailer의 chroot 안에 하드링크로) 준비해야 하는 요구사항(`docs/jailer.md:275-276`: "The user must create hard links for... any resources which will be provided to the VM")은 "전략 코드를 실행하는 것"과 "완전한 게스트 OS 이미지를 관리하는 것"이 하나의 작업으로 묶인다는 뜻 — Python 인터프리터 하나 격리시키자고 커널+rootfs 이미지 파이프라인을 새로 운영해야 한다.
- snapshot/restore API(`/snapshot/create`, `/snapshot/load`)를 활용하면 "기본 Python 런타임이 이미 부팅된 상태"를 스냅샷해 두고 복원하는 방식으로 콜드스타트를 줄일 수 있지만(파이어크래커 자체가 Lambda에서 쓰는 최적화 기법), 이는 AIOS가 스냅샷 관리라는 또 다른 운영 축을 새로 만들어야 함을 의미한다.

---

## 5. AIOS의 실제 니즈에 대한 직접 비교 — 위협 모델 적정성

AIOS가 막아야 할 대상은 "악의적인 게스트 OS가 하이퍼바이저를 뚫는 것"이 아니라 **"마켓플레이스 전략 플러그인이 예상 밖의 일을 하는 것"**이다. 이는 gVisor/Firecracker가 원래 상정한 멀티테넌트 클라우드 threat model(Sentry/KVM 문서가 반복해서 강조하는 "malicious guest kernel" 시나리오)보다 훨씬 좁은 문제다.

gVisor의 security.md 스스로도 이 구분을 인정한다: "*A sandbox is not a substitute for a secure architecture*"(`security.md:85`) — 즉 sandbox는 "호스트 커널을 지키는 것"이지 "애플리케이션 로직이 잘못된 API를 호출하는 것"을 막는 도구가 아니다. AIOS가 막고 싶은 것(허용된 마켓플레이스 API만 호출, 파일시스템 미접근, 네트워크는 화이트리스트된 엔드포인트만, 무한루프/과도한 CPU 사용 방지)은 사실 **애플리케이션 레벨 정책**이지 커널 익스플로잇 방어가 아니다.

이미 이 리서치 라인에서 확인된 두 가지 "적정 수준" 선례가 있다.

**(a) AgenticTrading/OBaI — 서브프로세스 + env 화이트리스트.** 서드파티 코드(`ai-hedge-fund`)를 별도 프로세스로 fork하고 전달 환경변수를 좁게 제한하는 방식.

`ext_agentictrading_obai.md:95-102` 인용
```python
_SUBPROCESS_ENV_KEYS = frozenset({
    "PATH", "TMPDIR", "LANG", "LC_ALL", "TZ",
    "SSL_CERT_FILE", ..., "FINANCIAL_DATASETS_API_KEY", "OPENROUTER_API_KEY",
})
```
(`adapter.py:52`) — 커널/하이퍼바이저 경계가 아니라 OS 프로세스 경계 + 환경변수 노출 최소화만으로 "credential이 서드파티 코드로 새지 않는다"는 목표를 달성한다.

**(b) QuantDinger — Python AST/builtin 화이트리스트 샌드박스.** 사용자 Python 코드를 프로세스조차 분리하지 않고 같은 인터프리터 안에서 실행하되, builtin과 AST 노드를 화이트리스트로 제한.

`ext_quantdinger.md:246-259` 인용
```python
# Whitelisted builtins (strict)
# Only pure computational builtins. No I/O, no introspection, no code gen.
_BUILTINS_WHITELIST: Set[str] = {
    'bool', 'int', 'float', 'complex', 'str', 'bytes', 'bytearray',
    'list', 'tuple', 'dict', 'set', 'frozenset',
    ...
```
이 접근은 gVisor/Firecracker와 정반대 극단이다 — 커널 syscall이 아니라 **언어 레벨에서 허용된 연산만 파싱을 통과**시키므로 오버헤드가 사실상 0에 가깝지만, Python 자체가 매우 표현력이 풍부한 언어라 화이트리스트에 구멍이 나면(예: 알려진 sandbox escape 패턴, `__subclasses__()`류 introspection 우회) 전체가 뚫릴 수 있다는 근본적 약점이 있다.

### 세 접근의 방어 깊이 대 오버헤드

| 접근 | 격리 단위 | 방어 대상 | 오버헤드 | 운영 복잡도 |
|---|---|---|---|---|
| QuantDinger AST whitelist | 언어(같은 프로세스) | 알려진 위험 API/introspection 호출 | 거의 0 | 낮음(라이브러리 하나) |
| subprocess + seccomp-bpf 직접 적용 | OS 프로세스 | 프로세스가 호출 가능한 syscall 집합 | 낮음 | 중간(seccomp 정책 작성/유지) |
| AgenticTrading subprocess + env allowlist | OS 프로세스 | 노출되는 환경변수/credential | 낮음 | 낮음(표준 `subprocess` 모듈) |
| gVisor(container) | Sentry(유저스페이스 커널) | 호스트 커널 syscall 전체 표면 | CPU-bound에는 낮음, syscall-heavy에는 큼 | 높음(컨테이너 오케스트레이터 필요) |
| Firecracker(microVM) | KVM 하드웨어 가상화 경계 | 호스트 커널 + 하이퍼바이저 이스케이프 | 부팅 125ms~초 단위, 상시로는 부적합 | 매우 높음(커널/rootfs/Jailer/네트워크 전체 스택) |

gVisor/Firecracker가 막는 것은 "전략 코드가 호스트 *커널*을 뚫고 나가는 것"이고, AIOS가 실제로 두려워해야 하는 것은 "전략 코드가 브로커 API에 예상 밖의 주문을 넣거나, 파일시스템/네트워크로 데이터를 유출하거나, 무한루프로 워커를 잠식하는 것"이다. 후자는 seccomp-bpf 직접 적용이나 `subprocess` + `resource`(RLIMIT_CPU/AS) + 명시적 syscall 화이트리스트만으로도 상당 부분 커버된다 — **커널 익스플로잇 방어(VM/Sentry급 방어)는 "멀티테넌트 클라우드가 서로 다른 고객의 완전히 신뢰할 수 없는 바이너리를 같은 하드웨어에서 돌릴 때" 필요한 수준이지, "AIOS가 검수한(정적 스캔+서명된) 마켓플레이스 전략 하나를 실행할 때" 필요한 수준이 아닐 가능성이 크다.**

**AIOS 시사점**
- 현재 AIOS는 §서두에서 언급했듯 코드 실행 표면이 **0**이다. 이 상태에서 gVisor/Firecracker 인프라(컨테이너 런타임, 커널/rootfs 이미지 파이프라인, Jailer 운영)를 먼저 구축하는 것은 전형적인 premature engineering이다 — 방어할 대상이 아직 존재하지 않는데 방어 인프라를 먼저 짓는 셈이다.
- Strategy Factory가 실제로 "richer strategy logic"(Python 표현식 평가기, 서드파티 플러그인)을 허용하기로 결정하는 시점에도, 첫 방어선은 QuantDinger식 AST whitelist(표현식 평가기 시나리오) 또는 AgenticTrading식 subprocess+env allowlist(서드파티 플러그인 시나리오)여야 한다 — 이 두 방식은 이미 검증된 오픈소스 선례가 있고, 구현/운영 비용이 gVisor/Firecracker 대비 한 자릿수 이상 낮다.
- gVisor/Firecracker는 "AIOS가 마켓플레이스를 진짜 서드파티에게 개방하고(자체 검수 없이 임의 코드 업로드 허용), 규제/컴플라이언스상 완전한 커널 격리를 증명해야 하는" 단계에 가서야 정당화된다. 그 전까지는 §최종 결론의 Tier 모델에서 Tier 2/3으로 남겨두는 것이 합리적이다.

---

## 최종 결론 — Sandbox Tier Model 제안

AIOS Execution Plane의 "Sandbox Tier Manager"가 실제로 설계 대상이 될 때를 대비해, 신뢰도-비용 축을 4단계로 제안한다.

### Tier 0 — 실행 없음 (현재 AIOS 상태)
- **내용**: 선언적 condition 문자열을 AIOS 자체 인터프리터가 평가. 사용자/LLM이 제출하는 것은 "조건식"이지 "코드"가 아니다.
- **방어 필요성**: 없음 — 애초에 임의 코드 실행 경로가 존재하지 않으므로 커널/프로세스 격리라는 개념 자체가 적용 대상이 아니다.
- **현재 위치**: AIOS는 지금 여기 있다. 이 상태에서는 Sandbox Tier Manager를 "설계"할 필요조차 없고, "자리만 잡아두고 PoC 전까지 보류"한 `AIOS_Target_Architecture_Freeze_v0.1_2026-09-03.md:147-148`의 결정은 타당하다.

### Tier 1 — subprocess + seccomp/AST whitelist (최저 비용)
- **내용**: (a) Python 표현식 평가기를 허용할 경우 QuantDinger식 AST 정적 검증 + builtin whitelist(`safe_exec.py` 패턴)를 같은 프로세스 안에서 적용. (b) 서드파티 플러그인 코드를 허용할 경우 AgenticTrading식 `subprocess` 격리 + 환경변수 allowlist + `resource.setrlimit`(CPU/메모리) + 직접 작성한 seccomp-bpf 프로파일(허용 syscall만 명시).
- **오버헤드**: 사실상 0(AST 방식) ~ 프로세스 fork 수준(subprocess 방식). 컨테이너 오케스트레이터 불필요.
- **약점**: 언어/OS 레벨 화이트리스트의 완전성에 의존 — "알려진 우회 기법이 없는가"를 지속적으로 검증해야 함. 완전한 커널 익스플로잇 방어는 아님.
- **권고**: **AIOS가 Strategy Factory에 코드 실행을 처음 도입할 때 지어야 할 것은 이 Tier다.** 이미 구현 참조가 두 개(QuantDinger, AgenticTrading) 확보되어 있고, 오늘 존재하지 않는 위협(신뢰할 수 없는 완전한 서드파티 바이너리)까지 상정한 과설계를 피할 수 있다.

### Tier 2 — gVisor 컨테이너 (중간 비용)
- **내용**: 마켓플레이스 전략을 `runsc` 런타임의 컨테이너 안에서 실행. CPU-bound·syscall-light라는 AIOS 워크로드 형태와 gVisor의 성능 프로파일이 잘 맞는다(§2).
- **오버헤드**: CPU 연산 자체는 네이티브와 거의 동일하지만, 컨테이너 이미지 빌드·레지스트리·오케스트레이터(Docker/containerd/K8s) 운영이 필요. syscall이 많아지면(파일 I/O 집약적 백테스트 등) 구조적 비용이 커진다.
- **트리거 조건**: AIOS가 (a) 검수 없이 서드파티 코드를 받기 시작하거나, (b) 규제 기관이 "커널 레벨 격리 증빙"을 요구하거나, (c) Tier 1의 AST/seccomp whitelist로는 막을 수 없는 표현력(임의 라이브러리 import, C 확장 모듈 등)을 마켓플레이스에 허용하기로 결정할 때.

### Tier 3 — Firecracker microVM (최고 비용)
- **내용**: 신뢰도가 가장 낮은 코드(예: 검증되지 않은 신규 마켓플레이스 발행자의 최초 제출물)를 완전한 하드웨어 가상화 경계 안에서, 1회성으로 실행.
- **오버헤드**: 125ms(하한, 최소 구성) ~ 초 단위(실측, 커널/rootfs 로딩 포함) 콜드스타트 — **매 tick/매 호출마다 실행되는 상시 워크로드에는 부적합.** AWS Lambda처럼 "저빈도·독립적 1회 실행"에 최적화된 도구다.
- **트리거 조건**: "이 마켓플레이스 플러그인을 처음 등록할 때 완전 격리 환경에서 정적/동적 분석을 수행"하는 **온보딩 심사 단계**처럼, 실행 빈도가 낮고 매번 완전히 새로운 신뢰 경계가 필요한 경우에 한정. 실시간 전략 실행 경로에 넣는 것은 설계 오류다.

### 권고: 지금 무엇을 지어야 하는가

AIOS는 현재 코드 실행 표면이 0인 상태(Tier 0)다. 다음에 지어야 할 것은 gVisor도 Firecracker도 아니라 **Tier 1**이다 — 이유는 (1) 아직 방어할 실제 위협(서드파티 임의 코드)이 존재하지 않고, (2) Tier 1은 이미 검증된 두 개의 오픈소스 선례를 그대로 참조 구현으로 쓸 수 있으며, (3) gVisor/Firecracker는 각각 "컨테이너 오케스트레이터 전체 스택" 또는 "커널+rootfs+Jailer+네트워크 전체 스택"이라는 무거운 운영 부담을 요구해 지금 단계의 AIOS 조직 규모/성숙도에 비대칭적으로 비싸다. Sandbox Tier Manager는 설계 문서상 "이름만 잡아두고 PoC 전까지 보류"라는 현재 결정이 정확하며, PoC의 첫 산출물은 Tier 1 구현이어야 하고 Tier 2/3은 Strategy Factory가 실제로 서드파티 코드를 받아들이기로 결정한 이후, 그것도 온보딩 심사(Tier 3)와 상시 실행(Tier 2)을 용도별로 분리해 재검토해야 한다.
