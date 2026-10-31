import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--portable-self-test":
        from smartcut.portable_check import check
        sys.exit(check(sys.argv[2]))
    from smartcut.app import main
    main()
