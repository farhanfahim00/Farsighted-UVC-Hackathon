"""The presentation layer works without a provider and without a main pipeline."""
from pathlib import Path
from tools.build_demo import generate, marked_word_diff


def test_word_diff_preserves_before_after_and_html_escapes():
    old, new = marked_word_diff('The limit shall be < 5 V', 'The limit shall be < 6 V')
    assert '&lt;' in old and '&lt;' in new
    assert '<del>5</del>' in old and '<ins>6</ins>' in new


def test_demo_generates_for_either_pipeline_and_preflight_only(tmp_path):
    out = tmp_path / 'preview.html'
    main = {'team_id': 'team-X', 'case_id': 'test', 'requirements': [
        {'id': 'R-1', 'text': 'A vehicle shall support braking </script> safely.',
         'source': {'regulation': 'UN RX', 'version': '2.0', 'paragraph': '5.1', 'page': 2}}]}
    index = [{'regulation': 'UN RX', 'versions': [{'version_label': '2.0',
             'files': [{'name': 'x.pdf', 'pdf_url': 'https://example.org/x.pdf'}]}]}]
    packets = [{'group': 'body:5.1', 'changed_provisions': [{
        'kind':'changed', 'old': {'text': 'Old test shall use 4 V.', 'label':'5.1','page':1},
        'new': {'text': 'New test shall use 5 V.', 'label':'5.1','page':2}}]}]
    generate(main, {}, [], packets, index, out,
             subgoal_files_index=[{'name':'new.pdf','pdf_url':'https://example.org/new.pdf'}])
    html = out.read_text(encoding='utf-8')
    assert '<title>Farsighted' in html
    assert 'A vehicle shall support braking' in html
    assert 'https://example.org/x.pdf' in html
    assert 'https://example.org/new.pdf' in html
    assert '\\u003c/script\\u003e' in html
    assert html.count('</script>') == 1  # no untrusted input can close the script
    assert 'No reviewed change submissions loaded yet' in html
