"""Exercise the production Swift downloader with an in-memory HTTP transport."""
from pathlib import Path
import subprocess,tempfile,tarfile,io,os,argparse
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path);args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='macos-installer-checks-') as folder:
    folder=Path(folder)
    for name,member in [('unicode','译研.app/Contents/资料 folder/测试.txt'),('traversal','译研.app/../outside.txt'),('foreign','Other.app/Contents/test.txt')]:
        with tarfile.open(folder/(name+'.tar.gz'),'w:gz') as archive:
            item=tarfile.TarInfo(member);data=b'verified archive fixture';item.size=len(data);archive.addfile(item,io.BytesIO(data))
    data=(folder/'unicode.tar.gz').read_bytes();middle=len(data)//2
    (folder/'first.part').write_bytes(data[:middle]);(folder/'second.part').write_bytes(data[middle:])
    (folder/'corrupt.tar.gz').write_bytes(b'not an archive')
    source=(ROOT/'desktop/macos-setup/InstallerCore.swift').read_text()
    needle='let config=installerNetworkConfiguration()\n                let queue=OperationQueue()'
    assert source.count(needle)==1
    # Replace only the network transport; manifest, streaming, retry and hash logic remain unchanged.
    (folder/'Core.swift').write_text(source.replace(needle,'let config=fixtureConfiguration()\n                let queue=OperationQueue()'))
    subprocess.run(['xcrun','swiftc','-swift-version','5','-O','-module-cache-path',str(ROOT/'.cache/macos-build/modules'),'-framework','AppKit','-framework','CryptoKit',str(ROOT/'desktop/macos-setup/DownloadNetwork.swift'),str(folder/'Core.swift'),str(ROOT/'tests/macos_installer_tests.swift'),'-o',str(folder/'checks')],check=True)
    subprocess.run([str(folder/'checks')],check=True,timeout=120,env={**os.environ,'PAPERBRIDGE_ARCHIVE_FIXTURES':str(folder)})
    if args.archive:
        subprocess.run([str(folder/'checks'),'--archive-smoke',str(args.archive.resolve()),str(folder/'actual-install')],check=True,timeout=300)
