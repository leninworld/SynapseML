"""Exact static WebsiteChannel projection helper reused from reviewed Q2; no old execution reopened."""
import hashlib

def verify_projection(input_root, output_root, tracked):
    files = sorted(path for path in input_root.rglob('*') if path.is_file())
    if not files or any(path.is_symlink() for path in input_root.rglob('*')):
        raise ValueError('Empty or symlinked doc input')
    generated = {str(path.relative_to(input_root).with_suffix('.md') if path.suffix == '.ipynb'
                     else path.relative_to(input_root)) for path in files}
    if len(generated) != len(files):
        raise ValueError('Output filename collision')
    expected = generated | set(tracked)
    actual = {str(path.relative_to(output_root)) for path in output_root.rglob('*') if path.is_file()}
    if actual != expected:
        raise ValueError('Incomplete or extra generated output: ' + repr(sorted(actual ^ expected)))
    for path in files:
        output = output_root / path.relative_to(input_root)
        if path.suffix == '.ipynb':
            output = output.with_suffix('.md')
            if not output.read_text().startswith('---\ntitle: '):
                raise ValueError('Missing generated frontmatter')
        elif hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(output.read_bytes()).digest():
            raise ValueError('Non-notebook copy differs')
    return {'input_files': len(files), 'output_files': len(actual),
            'notebooks': sum(path.suffix == '.ipynb' for path in files), 'tracked': sorted(tracked)}
