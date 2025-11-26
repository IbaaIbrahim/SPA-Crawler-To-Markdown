#!/usr/bin/env python3
"""Test script to verify duplicate URL prevention"""

from src.spa_crawler.utils import canonicalize

def test_canonicalization():
    """Test that URLs are properly canonicalized to prevent duplicates"""
    
    test_cases = [
        # Fragments should be removed
        ("https://pro.arcgis.com/en/pro-app/3.6/help/#section1", "https://pro.arcgis.com/en/pro-app/3.6/help/#section2", True),
        
        # Trailing slashes should be normalized
        ("https://example.com/page", "https://example.com/page/", True),
        
        # Case insensitive domain
        ("https://Example.com/page", "https://example.com/page", True),
        
        # Query parameter order
        ("https://example.com/page?a=1&b=2", "https://example.com/page?b=2&a=1", True),
        
        # Different paths are different
        ("https://example.com/page1", "https://example.com/page2", False),
        
        # Root path trailing slash is kept
        ("https://example.com/", "https://example.com", True),
    ]
    
    print("Testing URL Canonicalization")
    print("=" * 80)
    
    passed = 0
    failed = 0
    
    for url1, url2, should_be_same in test_cases:
        canon1 = canonicalize(url1)
        canon2 = canonicalize(url2)
        are_same = (canon1 == canon2)
        
        status = "✓ PASS" if are_same == should_be_same else "✗ FAIL"
        if are_same == should_be_same:
            passed += 1
        else:
            failed += 1
        
        print(f"\n{status}")
        print(f"  URL 1:   {url1}")
        print(f"  Canon 1: {canon1}")
        print(f"  URL 2:   {url2}")
        print(f"  Canon 2: {canon2}")
        print(f"  Expected: {'Same' if should_be_same else 'Different'}, Got: {'Same' if are_same else 'Different'}")
    
    print("\n" + "=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    
    return failed == 0

if __name__ == "__main__":
    success = test_canonicalization()
    exit(0 if success else 1)
